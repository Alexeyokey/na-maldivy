.PHONY: up down logs dev check check-fast test lint fmt destinations destinations-data schemas tool-examples u2-check u2-demo u2-compat-check

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

tool-examples:
	cd backend && uv run python ../scripts/export_tool_examples.py

u2-check:
	bash scripts/check_u2.sh

u2-demo:
	cd backend && CACHE_MODE=replay LLM_PROVIDER=fake uv run --locked --offline python -m app.u2_cli

u2-compat-check:
	cd backend && CACHE_MODE=replay LLM_PROVIDER=fake uv run --locked --offline pytest \
		tests/test_u2_booking_compatibility.py tests/test_data_services.py tests/test_s4_adapter.py \
		tests/test_unknown_hotel_times.py tests/test_hotels_data_and_search.py \
		tests/test_hotel_catalog_projection.py tests/test_transfer_builder_catalog.py
