.PHONY: d8-check d8-demo

d8-check:
	cd backend && uv run --locked --offline ruff check .
	cd backend && uv run --locked --offline ruff format --check .
	cd backend && LLM_PROVIDER=fake uv run --locked --offline pytest
	$(MAKE) d8-demo

d8-demo:
	cd backend && uv run --locked --offline python -m app.d8_cli
