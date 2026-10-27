.PHONY: demo-data-check demo-data-rehearse

demo-data-check:
	cd backend && uv run --locked --offline ruff check .
	cd backend && uv run --locked --offline ruff format --check .
	cd backend && LLM_PROVIDER=fake uv run --locked --offline pytest tests/test_u2_eval_cases.py tests/test_demo_preparation.py

demo-data-rehearse:
	cd backend && LLM_PROVIDER=fake uv run --locked --offline python ../scripts/rehearse_demo.py --output /tmp/na-maldivy-demo-data-rehearsals.json
