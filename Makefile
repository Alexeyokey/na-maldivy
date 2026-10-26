.PHONY: up down logs dev check check-fast test lint fmt schemas tool-examples

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

.PHONY: d2-check d2-test d2-demo d2-d4-test

d2-check:
	cd backend && uv run --locked --offline ruff check .
	cd backend && uv run --locked --offline ruff format --check .
	cd backend && LLM_PROVIDER=fake uv run --locked --offline pytest -rs
	cd backend && uv run --locked --offline python -m app.tools.schema --check
	cd backend && uv run --locked --offline python ../scripts/export_tool_examples.py --check
	$(MAKE) d2-demo

d2-test:
	cd backend && LLM_PROVIDER=fake uv run --locked --offline pytest tests/test_flights.py tests/test_d2_demo.py

d2-demo:
	cd backend && LLM_PROVIDER=fake uv run --locked --offline python -m app.d2_cli

d2-d4-test:
	cd backend && LLM_PROVIDER=fake uv run --locked --offline python ../scripts/check_d2_d4.py \
		--d4-ref origin/codex/D4-cache-currency-pr

schemas:
	cd backend && uv run python -m app.tools.schema

tool-examples:
	cd backend && uv run python ../scripts/export_tool_examples.py
