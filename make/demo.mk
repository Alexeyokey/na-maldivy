.PHONY: demo-data-check demo-data-rehearse demo-cache-check demo-rehearse u2-eval demo-up demo-down demo-dev offline-package submission-eval three-destination-rehearse

demo-up:
	docker compose --env-file /dev/null -f docker-compose.yml -f docker-compose.demo.yml up --build -d --wait

demo-down:
	docker compose --env-file /dev/null -f docker-compose.yml -f docker-compose.demo.yml down

demo-dev:
	cd backend && TOOLS_MODE=real FLIGHTS_MODE=demo TRAINS_MODE=synthetic CACHE_MODE=replay LLM_PROVIDER=fake \
		AVIASALES_CACHE_DIR=../data/demo/aviasales uv run --locked --offline uvicorn app.main:create_app --factory --reload

demo-cache-check:
	uv run --project backend --locked --offline python scripts/check_demo_cache.py

u2-eval:
	uv run --project backend --locked --offline python scripts/run_u2_eval.py \
		--today 2026-10-28 --output /tmp/na-maldivy-u2-chat-eval.json

demo-rehearse:
	uv run --project backend --locked --offline python scripts/rehearse_submission.py \
		--output-dir /tmp/na-maldivy-backend-rehearsals --count 5

demo-data-check:
	cd backend && uv run --locked --offline ruff check .
	cd backend && uv run --locked --offline ruff format --check .
	cd backend && LLM_PROVIDER=fake uv run --locked --offline pytest tests/test_u2_eval_cases.py tests/test_demo_preparation.py

demo-data-rehearse:
	cd backend && LLM_PROVIDER=fake uv run --locked --offline python ../scripts/rehearse_demo.py --output /tmp/na-maldivy-demo-data-rehearsals.json

offline-package:
	sh scripts/package_offline_demo.sh

submission-eval:
	uv run --project backend --locked --offline python scripts/run_submission_eval.py --today 2026-10-28 --output /tmp/na-maldivy-submission-eval.json

three-destination-rehearse:
	uv run --project backend --locked --offline python scripts/rehearse_three_destinations.py --output-dir /tmp/na-maldivy-three-destinations
