.PHONY: d3-data d3-check d3-compat-check

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
		tests/test_transfer_builder_catalog.py tests/test_s4_adapter.py tests/test_hotel_review_regressions.py
