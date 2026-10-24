"""OpenAI-совместимый клиент против подставного HTTP-сервера: SDK разбирает настоящие ответы в формате API."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.llm import LLMError, Message, OpenAICompatClient, ToolCall, ToolSpec

Handler = Callable[[httpx.Request], httpx.Response]
HELLO = [Message(role="user", content="Привет")]


def make_client(handler: Handler) -> OpenAICompatClient:
    return OpenAICompatClient(
        api_key="test-key",
        base_url="https://llm.test/v1",
        models={"main": "model-main", "fast": "model-fast"},
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def recorder(response: Callable[[], httpx.Response]) -> tuple[Handler, list[dict[str, Any]]]:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        bodies.append(json.loads(request.content))
        return response()

    return handler, bodies


def completion(message: dict[str, Any], choices: bool = True) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": "model-main",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}]
            if choices
            else [],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        },
    )


def chunk(content: str | None, choices: bool = True) -> dict[str, Any]:
    delta = {"content": content} if content is not None else {}
    return {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "model-main",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}] if choices else [],
    }


def sse_response(*chunks: dict[str, Any]) -> httpx.Response:
    body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


async def test_complete_returns_text_and_usage() -> None:
    handler, bodies = recorder(lambda: completion({"content": "Привет!"}))
    llm = make_client(handler)

    resp = await llm.complete(HELLO, tier="fast", temperature=0.2, max_tokens=100, json_mode=True)

    assert resp.text == "Привет!"
    assert resp.tool_calls == []
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (12, 3)
    assert bodies[0]["model"] == "model-fast"
    assert bodies[0]["messages"] == [{"role": "user", "content": "Привет"}]
    assert bodies[0]["temperature"] == 0.2
    assert bodies[0]["max_tokens"] == 100
    assert bodies[0]["response_format"] == {"type": "json_object"}


async def test_tool_calls_round_trip() -> None:
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "search_flights", "arguments": '{"to": "MLE"}'},
    }
    handler, bodies = recorder(lambda: completion({"content": None, "tool_calls": [tool_call]}))
    llm = make_client(handler)
    tool = ToolSpec(
        name="search_flights",
        description="Поиск рейсов",
        parameters={"type": "object", "properties": {"to": {"type": "string"}}},
    )

    resp = await llm.complete([Message(role="user", content="Рейсы на Мальдивы")], tools=[tool])

    assert resp.text is None
    assert resp.tool_calls == [ToolCall(id="call_1", name="search_flights", arguments={"to": "MLE"})]
    assert bodies[0]["tools"] == [
        {
            "type": "function",
            "function": {"name": "search_flights", "description": "Поиск рейсов", "parameters": tool.parameters},
        }
    ]

    history = [
        Message(role="user", content="Рейсы на Мальдивы"),
        Message(role="assistant", tool_calls=resp.tool_calls),
        Message(role="tool", tool_call_id="call_1", content='{"flights": []}'),
    ]
    await llm.complete(history, tools=[tool])

    assert bodies[1]["messages"][1:] == [
        {"role": "assistant", "content": None, "tool_calls": [tool_call]},
        {"role": "tool", "content": '{"flights": []}', "tool_call_id": "call_1"},
    ]


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]"])
async def test_bad_tool_arguments_raise(arguments: str) -> None:
    tool_call = {"id": "call_1", "type": "function", "function": {"name": "search", "arguments": arguments}}
    handler, _ = recorder(lambda: completion({"content": None, "tool_calls": [tool_call]}))

    with pytest.raises(LLMError, match="search"):
        await make_client(handler).complete(HELLO)


async def test_empty_choices_raise() -> None:
    handler, _ = recorder(lambda: completion({}, choices=False))

    with pytest.raises(LLMError, match="choices"):
        await make_client(handler).complete(HELLO)


async def test_stream_yields_text_chunks() -> None:
    handler, bodies = recorder(
        lambda: sse_response(chunk("При"), chunk("вет"), chunk(None), chunk(None, choices=False))
    )

    parts = [text async for text in make_client(handler).stream(HELLO)]

    assert parts == ["При", "вет"]
    assert bodies[0]["stream"] is True
    assert bodies[0]["model"] == "model-main"


def server_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"error": {"message": "boom"}})


def timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timeout", request=request)


@pytest.mark.parametrize("handler", [server_error, timeout])
async def test_provider_errors_become_llm_error(handler: Handler) -> None:
    llm = make_client(handler)

    with pytest.raises(LLMError, match="model-main"):
        await llm.complete(HELLO)
    with pytest.raises(LLMError, match="model-main"):
        [text async for text in llm.stream(HELLO)]
