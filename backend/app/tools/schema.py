"""Выгрузка общего JSON Schema входов/выходов D1 для обсуждения на O1."""

import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter
from pydantic.json_schema import GenerateJsonSchema

from app.tools.registry import TOOLS


def tools_schema() -> dict:
    inputs = []
    for name, tool in TOOLS.items():
        inputs.append((name + ":input", "validation", TypeAdapter(tool.input_model).core_schema))
        inputs.append((name + ":output", "serialization", tool.output_adapter.core_schema))
    references, definitions = GenerateJsonSchema().generate_definitions(inputs)
    properties = {}
    for name, tool in TOOLS.items():
        properties[name] = {
            "description": tool.description,
            "type": "object",
            "properties": {
                "input": references[(name + ":input", "validation")],
                "output": references[(name + ":output", "serialization")],
            },
            "required": ["input", "output"],
            "additionalProperties": False,
        }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "D1 travel tool contracts v0.1 (draft for O1; S4 feedback)",
        "$defs": definitions,
        "type": "object",
        "properties": properties,
        "required": list(TOOLS),
        "additionalProperties": False,
    }


def schema_json() -> str:
    return json.dumps(tools_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "contracts/schema/tools.json",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = args.output
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != schema_json():
            raise SystemExit("D1 schema differs; run make schemas")
        print("D1 draft schema is current")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(schema_json(), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
