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
	cd backend && uv run --offline --no-sync pytest tests/test_destination_catalogue.py tests/test_destinations.py tests/test_d5_adapter.py

