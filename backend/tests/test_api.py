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


def offline_client() -> TestClient:
    """Как без .env: фейковая модель, разбор правилами, тексты по шаблонам."""
    return TestClient(create_app(Settings(_env_file=None, llm_provider="fake")))


def test_chat_offline_agent_asks_then_plans() -> None:
    with offline_client() as client:
        events = parse_sse(client.post("/api/chat", json={"message": "Хочу на море в ноябре"}).text)
        names = [name for name, _ in events]
        assert names[0] == "status" and names[-1] == "done"
        question = next(data for name, data in events if name == "question")
        assert question["field"] == "origin"
        session_id = events[-1][1]["session_id"]

        events = parse_sse(client.post("/api/chat", json={"message": "из Москвы", "session_id": session_id}).text)
        assert events[-1] == ("done", {"session_id": session_id, "stage": "destinations_shown", "version": 2})
        assert any(name == "tool_call" and data["name"] == "suggest_destinations" for name, data in events)
        assert any(name == "options" for name, _ in events)

        state = client.get(f"/api/session/{session_id}").json()
        assert state["brief"]["origin"] == "MOW" and state["sources"]["month"] == "explicit"

        assert client.delete(f"/api/session/{session_id}").status_code == 204
        assert client.get(f"/api/session/{session_id}").status_code == 404


def test_chat_with_model_streams_its_text() -> None:
    analysis = {"intent": "plan", "patch": {"origin": "MOW", "destination": "AYT", "month": 11, "nights": 7}}
    llm = FakeLLMClient(
        [
            LLMResponse(text=json.dumps(analysis), model="fake-fast"),
            LLMResponse(text="Собрал три варианта в Анталье", model="fake-main"),
        ]
    )
    with make_client(llm) as client:
        resp = client.post("/api/chat", json={"message": "Из Москвы в Анталью в ноябре на неделю"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    assert events[-1][0] == "done"
    assert "".join(data["text"] for name, data in events if name == "delta") == "Собрал три варианта в Анталье"
    assert llm.calls[0][0].role == "system" and llm.calls[0][-1].content == "Из Москвы в Анталью в ноябре на неделю"


@pytest.mark.parametrize("message", ["", "x" * 4001])
def test_chat_rejects_bad_message(client: TestClient, message: str) -> None:
    assert client.post("/api/chat", json={"message": message}).status_code == 422


@pytest.mark.parametrize("before_failure", [[], ["Начинаю "]])
def test_chat_survives_llm_failure(before_failure: list[str]) -> None:
    """Модель не отвечает: разбор правилами, текст по шаблону, ход завершается done, а не error."""
    with make_client(FailingLLM(before_failure)) as client:
        events = parse_sse(client.post("/api/chat", json={"message": "Хочу на море в ноябре"}).text)

    assert events[-1][0] == "done"
    assert any(name == "warning" for name, _ in events)
    text = "".join(data["text"] for name, data in events if name == "delta")
    assert text.startswith("".join(before_failure)) and "Откуда" in text
