.PHONY: d2-check d2-test d2-demo d2-d4-test

d2-check:
	cd backend && uv run --locked --offline ruff check .
	cd backend && uv run --locked --offline ruff format --check .
	cd backend && LLM_PROVIDER=fake uv run --locked --offline pytest -rs
	cd backend && uv run --locked --offline python -m app.tools.schema --check
	cd backend && uv run --locked --offline python ../scripts/export_tool_examples.py --check
	$(MAKE) d2-demo

d2-test:
	cd backend && LLM_PROVIDER=fake uv run --locked --offline pytest tests/test_flights.py tests/test_d2_demo.py tests/test_flight_review_regressions.py

d2-demo:
	cd backend && LLM_PROVIDER=fake uv run --locked --offline python -m app.d2_cli

d2-d4-test:
	cd backend && LLM_PROVIDER=fake uv run --locked --offline python ../scripts/check_d2_d4.py \
		--d4-ref origin/codex/D4-cache-currency-pr

