.PHONY: d4-check d4-test d4-demo

d4-check:
	UV_OFFLINE=1 bash scripts/check.sh lint test
	$(MAKE) d4-demo

d4-test:
	cd backend && CACHE_MODE=replay LLM_PROVIDER=fake uv run --locked --offline pytest \
		tests/test_http_cache.py tests/test_currency.py tests/test_cbr_snapshot.py tests/test_d4_demo.py tests/test_cache_review_regressions.py

d4-demo:
	cd backend && CACHE_MODE=replay LLM_PROVIDER=fake uv run --locked --offline python -m app.d4_cli
