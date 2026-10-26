.PHONY: up down logs dev check check-fast test lint fmt d4-check d4-test d4-demo

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

d4-check:
	UV_OFFLINE=1 bash scripts/check.sh lint test
	$(MAKE) d4-demo

d4-test:
	cd backend && CACHE_MODE=replay LLM_PROVIDER=fake uv run --locked --offline pytest \
		tests/test_http_cache.py tests/test_currency.py tests/test_cbr_snapshot.py tests/test_d4_demo.py

d4-demo:
	cd backend && CACHE_MODE=replay LLM_PROVIDER=fake uv run --locked --offline python -m app.d4_cli
