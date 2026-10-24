import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import FakeLLMClient, LLMError, LLMResponse, Message
from app.main import create_app


class FailingLLM(FakeLLMClient):
    """Отдаёт заготовленные куски текста, потом падает, как провайдер на таймауте."""

    def __init__(self, before_failure: list[str]) -> None:
        super().__init__()
        self._before_failure = before_failure

    async def stream(self, messages: list[Message], **kwargs: Any) -> AsyncIterator[str]:
        for text in self._before_failure:
            yield text
        raise LLMError("timeout")


def make_client(llm: FakeLLMClient) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, llm_provider="fake"), llm=llm))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with make_client(FakeLLMClient()) as c:
        yield c


def parse_sse(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((fields.get("event", "message"), json.loads(fields.get("data", "null"))))
    return events


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "llm_provider": "fake"}


def test_chat_streams_answer_then_done() -> None:
    llm = FakeLLMClient([LLMResponse(text="Тёплое море в ноябре: Мальдивы", model="fake-main")])
    with make_client(llm) as client:
        resp = client.post("/api/chat", json={"message": "Хочу на море"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    assert events[-1] == ("done", {})
    assert {name for name, _ in events[:-1]} == {"delta"}
    assert "".join(data["text"] for _, data in events[:-1]) == "Тёплое море в ноябре: Мальдивы"
    sent = llm.calls[0]
    assert [m.role for m in sent] == ["system", "user"]
    assert sent[1].content == "Хочу на море"


@pytest.mark.parametrize("message", ["", "x" * 4001])
def test_chat_rejects_bad_message(client: TestClient, message: str) -> None:
    assert client.post("/api/chat", json={"message": message}).status_code == 422


@pytest.mark.parametrize("before_failure", [[], ["Начинаю "]])
def test_chat_reports_llm_failure(before_failure: list[str]) -> None:
    with make_client(FailingLLM(before_failure)) as client:
        events = parse_sse(client.post("/api/chat", json={"message": "Привет"}).text)

    assert [data["text"] for name, data in events if name == "delta"] == before_failure
    assert events[-1][0] == "error"
    assert "не отвечает" in events[-1][1]["message"]
