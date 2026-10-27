.PHONY: d7-check d7-build

d7-check:
	cd backend && uv run --locked --offline ruff check .
	cd backend && uv run --locked --offline ruff format --check .
	cd backend && LLM_PROVIDER=fake uv run --locked --offline pytest
	cd backend && uv run --locked --offline python ../scripts/build_hotel_profiles.py --check
	cd backend && uv run --locked --offline python -m app.tools.schema --check
	cd backend && uv run --locked --offline python ../scripts/export_tool_examples.py --check

d7-build:
	cd backend && uv run --locked --offline python ../scripts/build_hotel_profiles.py --generate-reviews
