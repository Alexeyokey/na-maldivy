#!/usr/bin/env bash
# Проверки перед PR, те же, что в CI: bash scripts/check.sh [lint] [test] [smoke]. Без аргументов — все.
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
  steps=(lint test smoke)
fi

# Смоук идёт отдельным compose-проектом на своём порту, чтобы не трогать запущенный make up
SMOKE_PROJECT=na-maldivy-check
SMOKE_PORT=18000

smoke_compose() {
  AGENT_PORT=$SMOKE_PORT docker compose -f docker-compose.yml -f docker-compose.demo.yml -p "$SMOKE_PROJECT" "$@"
}

smoke() {
  trap 'smoke_compose down >/dev/null 2>&1' EXIT
  if ! smoke_compose up --build -d --wait --wait-timeout 120; then
    smoke_compose logs
    exit 1
  fi
  curl -fsS "localhost:$SMOKE_PORT/health"
  echo
  local out
  out=$(curl -fsS -N "localhost:$SMOKE_PORT/api/chat" -H 'Content-Type: application/json' -d '{"message": "Привет"}')
  if ! grep -q '^event: done' <<<"$out"; then
    echo "Чат не дошёл до события done:" >&2
    echo "$out" >&2
    smoke_compose logs
    exit 1
  fi
}

for step in "${steps[@]}"; do
  echo "== $step"
  case "$step" in
    lint) (cd backend && uv sync --locked && uv run ruff check . && uv run ruff format --check .) ;;
    test) (cd backend && uv sync --locked && uv run pytest) ;;
    smoke) smoke ;;
    *)
      echo "Неизвестный шаг: $step. Есть lint, test, smoke." >&2
      exit 2
      ;;
  esac
done

echo "Проверки прошли: ${steps[*]}"
