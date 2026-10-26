import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from app.tools.schema import tools_schema

ROOT = Path(__file__).resolve().parents[2]


def test_checked_examples_are_reproducible_and_validate_against_export():
    subprocess.run([sys.executable, str(ROOT / "scripts/export_tool_examples.py"), "--check"], check=True, cwd=ROOT)
    examples = json.loads((ROOT / "contracts/examples/tools.json").read_text())
    Draft202012Validator(tools_schema(), format_checker=FormatChecker()).validate(examples)
    assert isinstance(examples["get_transfer"]["output"], list)
    cases = json.loads((ROOT / "contracts/examples/s4-transfers.json").read_text())
    night = next(case for case in cases if case["id"] == "male-night-seaplane")
    plane = next(item for item in night["output"] if item["mode"] == "seaplane")
    assert plane["available"] is False
    assert plane["depart_at"] == "2026-11-19T06:00:00+05:00"
    assert plane["hours"] == {"from": "06:00:00", "to": "18:00:00"}
