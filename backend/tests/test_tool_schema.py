"""Публичная выгрузка должна описывать реальные пары вход/выход всех инструментов."""

from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from app.tools import TOOLS, run_tool
from app.tools.schema import schema_json, tools_schema


def test_export_is_valid_and_up_to_date() -> None:
    Draft202012Validator.check_schema(tools_schema())
    path = Path(__file__).resolve().parents[2] / "contracts/schema/tools.json"
    assert path.read_text(encoding="utf-8") == schema_json(), "Обновите схемы: make schemas"


async def test_every_registered_tool_runs_and_round_trips_through_exported_schema() -> None:
    inputs = {
        "search_flights": {"origin": "SVO", "destination": "DXB", "departure_date": "2026-11-18"},
        "search_hotels": {"destination": "DXB", "check_in": "2026-11-18", "check_out": "2026-11-25"},
        "search_trains": {"origin": "MOW", "destination": "LED", "departure_date": "2026-11-18"},
        "suggest_destinations": {"month": 11, "min_water_temperature_c": 24},
        "price_calendar": {"origin": "SVO", "destination": "DXB", "month": "2026-11"},
    }
    flights = await run_tool("search_flights", inputs["search_flights"])
    hotels = await run_tool("search_hotels", inputs["search_hotels"])
    inputs["get_transfer"] = {
        "origin": flights[0].destination.model_dump(mode="json"),
        "destination": hotels[0].place.model_dump(mode="json"),
        "pickup_at": flights[0].arrival_at.isoformat(),
    }
    examples = {}
    for name, arguments in inputs.items():
        tool = TOOLS[name]
        result = await run_tool(name, arguments)
        assert result  # У каждого инструмента есть содержательный демо-ответ.
        encoded = tool.output_adapter.dump_json(result)
        assert tool.output_adapter.validate_json(encoded) == result
        examples[name] = {"input": arguments, "output": tool.output_adapter.dump_python(result, mode="json")}

    Draft202012Validator(tools_schema(), format_checker=FormatChecker()).validate(examples)


async def test_output_validation_does_not_trust_mutated_contract_instances() -> None:
    arguments = {"origin": "SVO", "destination": "DXB", "departure_date": "2026-11-18"}
    flights = await run_tool("search_flights", arguments)
    flights[0].price_rub = -1
    with pytest.raises(ValidationError):
        TOOLS["search_flights"].output_adapter.validate_python(flights)
