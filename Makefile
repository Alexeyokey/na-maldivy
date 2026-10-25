.PHONY: up down logs dev check check-fast test lint fmt destinations destinations-data schemas u2-check u2-demo

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

destinations:
	cd backend && uv run python -m app.destination_cli --month 11 --tag beach --min-water 24

destinations-data:
	cd backend && uv run python ../scripts/build_destinations.py

schemas:
	cd backend && uv run python -m app.tools.schema

u2-check:
	bash scripts/check_u2.sh

u2-demo:
	cd backend && CACHE_MODE=replay LLM_PROVIDER=fake uv run --locked --offline python -m app.u2_cli
