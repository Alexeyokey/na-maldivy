.PHONY: up down logs dev check check-fast test lint fmt schemas tool-examples

# Независимые инструменты добавляют команды без конфликтов в корневом Makefile.
-include make/*.mk

up:
	docker compose up --build -d --wait

down:
	docker compose down

logs:
	docker compose logs -f

dev:
	cd backend && uv run uvicorn app.main:create_app --factory --reload

# Всё, что раньше делал CI: линтер, тесты, docker compose и смоук. Запускайте перед каждым PR.
check:
	bash scripts/check.sh

# То же без Docker, быстрее
check-fast:
	bash scripts/check.sh lint test

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

fmt:
	cd backend && uv run ruff format . && uv run ruff check --fix .

schemas:
	cd backend && uv run python -m app.tools.schema

tool-examples:
	cd backend && uv run python ../scripts/export_tool_examples.py

# Интерфейс без установки зависимостей: прокси /api/chat и /health к agent.
.PHONY: web web-demo web-test
web:
	cd frontend && npm run dev

web-demo:
	cd frontend && CHAT_MODE=demo npm run dev

web-test:
	cd frontend && npm run check && npm test && npm run test:integration

# Проверка уже запущенного стандартного демо; Python на компьютере не нужен.
.PHONY: judge-check judge-resume
judge-check:
	docker compose exec -T agent python /app/scripts/check_running_three_destinations.py --base-url http://app:8080 --context /state/judge-three-private.json --output /state/judge-three-report.json

# Сначала перезапустите agent и дождитесь healthy; контекст остаётся в томе.
judge-resume:
	docker compose exec -T agent python /app/scripts/check_running_three_destinations.py --base-url http://app:8080 --context /state/judge-three-private.json --output /state/judge-three-resume.json --resume

.PHONY: demo-generated-check expanded-demo-check
# These only validate committed public fixtures; no credentials or live model.
demo-generated-check:
	cd backend && uv run python ../scripts/build_destination_names.py --check
	cd backend && uv run python ../scripts/build_demo_hotels.py --check
	cd backend && uv run python ../scripts/build_demo_transport.py --check

expanded-demo-check: demo-generated-check
	cd backend && uv run python ../scripts/check_expanded_demo.py
