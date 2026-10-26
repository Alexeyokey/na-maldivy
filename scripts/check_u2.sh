#!/usr/bin/env bash
# Установка зависимостей: cd backend && uv sync --locked. Сами проверки без сети.
set -euo pipefail
cd "$(dirname "$0")/../backend"
export CACHE_MODE=replay
export LLM_PROVIDER=fake
uv run --locked --offline ruff check .
uv run --locked --offline ruff format --check .
uv run --locked --offline pytest
uv run --locked --offline python ../scripts/build_destinations.py --check
uv run --locked --offline python ../scripts/build_hotel_profiles.py --check
uv run --locked --offline python -m app.tools.schema --check
uv run --locked --offline python ../scripts/export_tool_examples.py --check
uv run --locked --offline python -m app.u2_cli
