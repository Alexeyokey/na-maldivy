.PHONY: protection-demo

# protection: демо «Отпуск под защитой» — отмена рейса и пересборка. Флаги: ARGS="--delay 5" или ARGS=--inbound
protection-demo:
	cd backend && uv run python scripts/protection_demo.py $(ARGS)
