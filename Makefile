.PHONY: up down logs dev check check-fast test lint fmt

up:
	docker compose up --build -d --wait

down:
	docker compose down

logs:
	docker compose logs -f

dev:
	cd backend && uv run uvicorn app.main:create_app --factory --reload

.PHONY: destinations destinations-data destinations-check

DESTINATIONS_ARGS ?= --month 11 --tag beach --min-water 24

# D5: сохранённый справочник; сначала подготовьте backend-окружение через uv sync.
destinations:
	cd backend && uv run --offline --no-sync python -m app.destination_cli $(DESTINATIONS_ARGS)

destinations-data:
	cd backend && uv run --offline --no-sync python ../scripts/build_destinations.py

# Проверка сохранённого YAML и только D5-тесты, без сети и синхронизации окружения.
destinations-check:
	cd backend && uv run --offline --no-sync python ../scripts/build_destinations.py --check
	cd backend && uv run --offline --no-sync pytest tests/test_destination_catalogue.py tests/test_destinations.py

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
