.PHONY: up down logs dev check check-fast test lint fmt schemas tool-examples d3-data d3-check d3-compat-check

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

d3-data:
	cd backend && uv run --locked --offline python ../scripts/fetch_hotels.py
	cd backend && uv run --locked --offline python ../scripts/build_hotel_rules.py

d3-check:
	cd backend && uv run --locked --offline ruff check .
	cd backend && uv run --locked --offline ruff format --check .
	cd backend && LLM_PROVIDER=fake uv run --locked --offline pytest
	cd backend && uv run --locked --offline python ../scripts/fetch_hotels.py --check
	cd backend && uv run --locked --offline python ../scripts/build_hotel_rules.py --check
	cd backend && uv run --locked --offline python -m app.tools.schema --check
	cd backend && uv run --locked --offline python ../scripts/export_tool_examples.py --check

d3-compat-check:
	cd backend && LLM_PROVIDER=fake uv run --locked --offline pytest \
		tests/test_d3_s4_compatibility.py tests/test_hotels_data_and_search.py \
		tests/test_hotel_catalog_projection.py tests/test_hotel_rules_builder.py \
		tests/test_transfer_builder_catalog.py tests/test_s4_adapter.py
