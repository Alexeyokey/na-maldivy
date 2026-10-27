.PHONY: d6-check d6-demo

d6-check:
	cd backend && uv run --locked --offline ruff check .
	cd backend && uv run --locked --offline ruff format --check .
	cd backend && LLM_PROVIDER=fake uv run --locked --offline pytest
	$(MAKE) d6-demo

d6-demo:
	cd backend && uv run --locked --offline python -m app.d6_cli
