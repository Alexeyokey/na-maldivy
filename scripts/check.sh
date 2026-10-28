#!/usr/bin/env bash
# Проверки перед PR, те же, что в CI: bash scripts/check.sh [lint] [test] [web] [smoke]. Без аргументов — все.
set -euo pipefail
cd "$(dirname "$0")/.."

# Проверки воспроизводят записи и используют фейковую модель независимо от .env.
export CACHE_MODE=replay
export LLM_PROVIDER=fake
export TOOLS_MODE=stub
export FLIGHTS_MODE=synthetic
export TRAINS_MODE=synthetic

steps=("$@")
if [ ${#steps[@]} -eq 0 ]; then
  steps=(lint test web smoke)
fi

# Проверяем тот же двухсервисный пакет, который получает жюри. Сборка видит
# рабочее содержимое tracked-файлов, но не локальные секреты/зависимости.
smoke() (
  SMOKE_PROJECT="na-maldivy-check-$$"
  SMOKE_PORT="${NA_MALDIVY_CHECK_PORT:-18080}"
  case "$SMOKE_PORT" in
    ''|*[!0-9]*) echo "NA_MALDIVY_CHECK_PORT должен быть числом от 1 до 65535." >&2; exit 2 ;;
  esac
  if [ "${#SMOKE_PORT}" -gt 5 ] || [ "$SMOKE_PORT" -lt 1 ] || [ "$SMOKE_PORT" -gt 65535 ]; then
    echo "NA_MALDIVY_CHECK_PORT должен быть числом от 1 до 65535." >&2
    exit 2
  fi
  SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/na-maldivy-check.XXXXXX")"
  SMOKE_DIR="$(cd "$SMOKE_DIR" && pwd -P)"
  SMOKE_READY=0

  smoke_compose() {
    # Keep only Docker connectivity and basic executable lookup. Provider keys,
    # COMPOSE_FILE, shell mode overrides and the owner's .env are not inherited.
    env -i PATH="$PATH" HOME="${HOME:-}" \
      DOCKER_HOST="${DOCKER_HOST:-}" DOCKER_CONTEXT="${DOCKER_CONTEXT:-}" \
      DOCKER_CONFIG="${DOCKER_CONFIG:-${HOME:-}/.docker}" \
      DOCKER_API_VERSION="${DOCKER_API_VERSION:-}" \
      APP_PORT="$SMOKE_PORT" TOOLS_MODE=real FLIGHTS_MODE=demo \
      CACHE_MODE=replay LLM_PROVIDER=fake TRAINS_MODE=synthetic \
      LLM_API_KEY= AVIASALES_API_TOKEN= YANDEX_RASP_API_KEY= CODEX_LOCAL_TOKEN= \
      docker compose --env-file "$SMOKE_DIR/.env" \
      -f "$SMOKE_DIR/docker-compose.yml" -p "$SMOKE_PROJECT" "$@"
  }

  cleanup_smoke() {
    result=$?
    trap - EXIT INT TERM
    if [ "$result" -ne 0 ] && [ "$SMOKE_READY" -eq 1 ]; then
      echo "Демо-проверка не завершилась. Диагностика отдельного тестового проекта:" >&2
      smoke_compose logs --no-color --tail 80 app agent >&2 || true
      # Only this safe summary is shown; the private context is never printed.
      smoke_compose exec -T agent cat /state/check-report.json >&2 || true
    fi
    if [ "$SMOKE_READY" -eq 1 ]; then
      smoke_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    fi
    rm -rf -- "$SMOKE_DIR"
    exit "$result"
  }
  trap cleanup_smoke EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  git ls-files --cached -z > "$SMOKE_DIR/index-files"
  while IFS= read -r -d '' filename; do
    case "$filename" in
      .env.example|*/.env.example) ;;
      .env|.env.*|*/.env|*/.env.*|.git|.git/*|*/.git/*|artifacts/*|.runtime/*|*/.runtime/*|.venv/*|*/.venv/*|node_modules/*|*/node_modules/*|__pycache__/*|*/__pycache__/*|*.pyc) continue ;;
    esac
    if [ ! -f "$filename" ] || [ -L "$filename" ]; then
      echo "Смоук требует обычные tracked-файлы без внешних symlink/submodule." >&2
      exit 1
    fi
    printf '%s\0' "$filename"
  done < "$SMOKE_DIR/index-files" > "$SMOKE_DIR/public-files"
  tar --null -T "$SMOKE_DIR/public-files" -cf - | tar -C "$SMOKE_DIR" -xf -
  rm "$SMOKE_DIR/index-files" "$SMOKE_DIR/public-files"
  cp "$SMOKE_DIR/.env.example" "$SMOKE_DIR/.env"
  cd "$SMOKE_DIR"
  SMOKE_READY=1

  echo "Проверяю стандартный Docker-пакет на порту $SMOKE_PORT."
  smoke_compose up --build -d --wait --wait-timeout 180 app agent
  smoke_compose exec -T agent python /app/scripts/check_running_three_destinations.py \
    --base-url http://app:8080 --context /state/check-private.json --output /state/check-report.json
  echo "Проверяю сохранность заказов после перезапуска агента."
  smoke_compose restart agent
  smoke_compose up -d --wait --wait-timeout 120 --no-build app agent
  smoke_compose exec -T agent python /app/scripts/check_running_three_destinations.py \
    --base-url http://app:8080 --context /state/check-private.json --output /state/check-report.json --resume
)

for step in "${steps[@]}"; do
  echo "== $step"
  case "$step" in
    lint) (cd backend && uv sync --locked && uv run ruff check . && uv run ruff format --check .) ;;
    test) (cd backend && uv sync --locked && uv run pytest) ;;
    web) (cd frontend && npm run check && npm test && npm run test:integration) ;;
    smoke) smoke ;;
    *)
      echo "Неизвестный шаг: $step. Есть lint, test, web, smoke." >&2
      exit 2
      ;;
  esac
done

echo "Проверки прошли: ${steps[*]}"
