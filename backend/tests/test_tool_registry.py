"""Контракты реестра и передача настоящих схем в LLM-клиент без сети."""

import json
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.config import Settings
from app.llm import Message, OpenAICompatClient
from app.tools.registry import TOOLS, create_tools, run_tool, tool_specs

EXAMPLES = {
    "search_flights": {
        "origin": "SVO",
        "destination": "AYT",
        "departure_date": "2026-11-15",
        "travelers": {"adults": 2, "children_ages": [7], "pets": 0},
    },
    "search_hotels": {"destination": "AYT", "check_in": "2026-11-15", "check_out": "2026-11-22"},
    "search_trains": {"origin": "LED", "destination": "MOW", "departure_date": "2026-11-15"},
    "get_transfer": {
        "origin": {
            "kind": "airport",
            "iata": "AYT",
            "name": "Аэропорт Антальи",
            "latitude": 36.8987,
            "longitude": 30.8005,
            "timezone": "Europe/Istanbul",
        },
        "destination": {
            "kind": "hotel",
            "id": "demo-ayt-budget",
            "iata": "AYT",
            "name": "Demo Hotel",
            "latitude": 36.9007,
            "longitude": 30.8025,
            "timezone": "Europe/Istanbul",
        },
        "pickup_at": "2026-11-15T12:00:00+03:00",
    },
    "suggest_destinations": {"month": 11},
    "price_calendar": {"origin": "SVO", "destination": "AYT", "month": "2026-11"},
}


def test_all_six_input_schemas_resolve_and_validate_examples() -> None:
    assert set(TOOLS) == set(EXAMPLES)

    for spec in tool_specs():
        Draft202012Validator.check_schema(spec.parameters)
        Draft202012Validator(spec.parameters).validate(EXAMPLES[spec.name])
        TOOLS[spec.name].input_model.model_validate(EXAMPLES[spec.name])


async def test_transfer_registry_example_returns_a_valid_nonempty_alternatives_list() -> None:
    alternatives = await run_tool("get_transfer", EXAMPLES["get_transfer"])
    assert isinstance(alternatives, list) and alternatives
    assert alternatives == sorted(alternatives, key=lambda item: (not item.available, item.price_rub, item.id))


async def test_generated_specs_reach_openai_compatible_api_unchanged() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "completion-1",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Готово"}}
                ],
            },
        )

    client = OpenAICompatClient(
        api_key="test-key",
        base_url="https://llm.test/v1",
        models={"main": "test-model", "fast": "test-model"},
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    specs = tool_specs()
    try:
        result = await client.complete([Message(role="user", content="На море")], tools=specs)
    finally:
        await client.aclose()

    assert result.text == "Готово"
    assert bodies[0]["tools"] == [{"type": "function", "function": spec.model_dump()} for spec in specs]


class ProbeInput(BaseModel):
    number: int


class ProbeOutput(BaseModel):
    doubled: int


class ProbeTool:
    name = "probe"
    description = "Test dispatch validation"
    input_model = ProbeInput
    output_adapter = TypeAdapter(ProbeOutput)

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[ProbeInput] = []

    async def run(self, input: ProbeInput) -> dict[str, Any]:
        self.calls.append(input)
        return self.response


async def test_dispatch_validates_input_before_call_and_output_after_call() -> None:
    probe = ProbeTool({"doubled": 4})
    registry = {probe.name: probe}
    with pytest.raises(ValidationError):
        await run_tool("probe", {"number": "not a number"}, tools=registry)
    assert probe.calls == []

    result = await run_tool("probe", {"number": 2}, tools=registry)
    assert probe.calls == [ProbeInput(number=2)]
    assert result == ProbeOutput(doubled=4)

    probe.response = {"doubled": "invalid"}
    with pytest.raises(ValidationError):
        await run_tool("probe", {"number": 2}, tools=registry)
    assert len(probe.calls) == 2


async def test_unknown_tool_and_empty_registry_do_not_fall_back_to_defaults() -> None:
    with pytest.raises(KeyError):
        await run_tool("missing", {})
    with pytest.raises(KeyError):
        await run_tool("search_flights", EXAMPLES["search_flights"], tools={})
    assert tool_specs({}) == []


def test_tools_mode_default_env_and_explicit_unavailable_real(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    settings = Settings(_env_file=None)
    assert settings.tools_mode == "stub"
    assert set(create_tools(settings)) == set(EXAMPLES)

    monkeypatch.setenv("TOOLS_MODE", "real")
    settings = Settings(_env_file=None)
    assert settings.tools_mode == "real"
    with pytest.raises(NotImplementedError, match="TOOLS_MODE=real"):
        create_tools(settings)

    monkeypatch.setenv("TOOLS_MODE", "typo")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
