#!/usr/bin/env bash
# Requires a checkout containing orchestrator and the data PRs; makes no network requests.
set -euo pipefail
cd "$(dirname "$0")/../backend"
uv run python - <<'PY'
from importlib.util import find_spec

from app.config import Settings
from app.tools import create_tools

for module in ("d5_adapter", "flights", "hotels", "trains", "price_calendar", "hotel_profiles"):
    if find_spec(f"app.tools.{module}") is None:
        raise SystemExit(f"Missing app.tools.{module}; combine data PRs #54-61 first")
tools = create_tools(Settings(_env_file=None, tools_mode="real"))
assert set(tools) == {
    "suggest_destinations", "search_flights", "search_hotels",
    "search_trains", "get_transfer", "price_calendar",
}, "Expected six real data adapters"
print("All six data adapters present; running agent/HTTP integration without network or LLM")
PY
uv run pytest tests/test_agent_tool_compatibility.py tests/test_agent_real_registry.py -q
