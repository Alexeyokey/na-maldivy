"""The search-only boundary is server enforced and part of retry identity."""

import hashlib
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent import AgentEvent
from app.chat_recovery import ChatRecovery, ChatRecoveryError, ChatRequestLedger, payload_fingerprint
from app.main import create_app
from app.submission_rehearsal import forbid_external_network
from app.three_destination_rehearsal import mixed_demo_settings
from app.u2_eval import parse_sse

ROOT = Path(__file__).resolve().parents[2]
MAIN = "Из Москвы в Анталью 18 ноября 2026 на неделю вдвоём"


@pytest.fixture
def client(tmp_path):
    settings = mixed_demo_settings(ROOT / "data/demo/aviasales", tmp_path / "checkout.sqlite3")
    with forbid_external_network() as network, TestClient(create_app(settings)) as connected:
        connected.app.state.agent._today = lambda: date(2026, 10, 29)
        yield connected
        assert network["blocked_attempts"] == 0


def chat(client, text, sid=None, **extra):
    body = {"message": text, **({"session_id": sid} if sid else {}), **extra}
    response = client.post("/api/chat", json=body)
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1]["type"] == "done" and not any(row["type"] == "error" for row in events)
    state = events[-1]["data"]
    assert client.get(f"/api/session/{state['session_id']}").json() == state
    return state, events, response.text


@pytest.mark.parametrize("message", ["Хочу второй вариант", "2 вариант", "да", "окей", "подтверждаю."])
@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("recoverable", [False, True])
def test_search_only_never_advances_selection_or_checkout_in_legacy_or_recoverable_http(
    client, message, selected, recoverable
):
    state, _, _ = chat(client, MAIN)
    sid = state["session_id"]
    if selected:
        chosen = next(row for row in state["options"]["items"] if row["can_confirm"])
        state, _, _ = chat(client, f"Беру вариант {chosen['index']}", sid)
        assert state["stage"] == "awaiting_confirmation" and state["booking"] is None
    after, events, _ = chat(
        client, message, sid, search_only=True, **({"request_id": "search-guard-action"} if recoverable else {})
    )
    assert after == state
    assert not any(row["type"] == "booking" for row in events)
    if selected or message in {"Хочу второй вариант", "2 вариант"}:
        answer = "".join(row["data"]["text"] for row in events if row["type"] == "delta")
        assert "Проверьте условия" in answer


@pytest.mark.parametrize("intent", ["select", "confirm"])
def test_search_only_blocks_arbitrary_model_selection_intent_independent_of_wording(client, intent):
    from test_agent_trip_changes import MisclassifiedAnalysis

    state, _, _ = chat(client, MAIN)
    sid = state["session_id"]
    agent = client.app.state.agent
    agent._llm, agent._llm_writes = MisclassifiedAnalysis(intent), True
    after, events, _ = chat(client, "Пожалуй, средний вариант, продолжим", sid, search_only=True)
    assert after == state and after["selected"] is after["booking"] is None
    assert "Проверьте условия" in "".join(row["data"]["text"] for row in events if row["type"] == "delta")


def test_search_only_allows_new_search_changes_and_read_only_questions(client):
    state, _, _ = chat(client, MAIN, search_only=True)
    sid = state["session_id"]
    changed, _, _ = chat(client, "Теперь 20 ноября 2026, трое взрослых", sid, search_only=True)
    assert changed["brief"]["start_date"] == "2026-11-20" and changed["brief"]["adults"] == 3
    assert changed["options"]["items"] and changed["selected"] is changed["booking"] is None
    for question in ("Почему отель невозвратный?", "А если сдвинуть даты, сколько сэкономим?", "Что говорят отзывы?"):
        reviewed, events, _ = chat(client, question, sid, search_only=True)
        assert reviewed == changed and not any(row["type"] == "booking" for row in events)
        if "отзывы" in question:
            assert any(row["type"] == "hotel_profile" for row in events)
        if "сдвинуть" in question:
            assert any(row["type"] == "date_savings" for row in events)


def test_false_or_absent_flag_keeps_ordinary_selection_and_confirmation_working(client):
    state, _, _ = chat(client, MAIN)
    sid = state["session_id"]
    chosen = next(row for row in state["options"]["items"] if row["can_confirm"])
    selected, _, _ = chat(client, f"Беру вариант {chosen['index']}", sid, search_only=False)
    assert selected["stage"] == "awaiting_confirmation"
    confirmed, events, _ = chat(client, "Подтверждаю", sid)
    assert confirmed["stage"] == "checkout_pending" and confirmed["booking"]
    assert any(row["type"] == "booking" for row in events)


@pytest.mark.parametrize("first_flag", [False, True])
def test_changed_search_only_flag_conflicts_before_another_agent_call_and_same_flag_replays(
    client, monkeypatch, first_flag
):
    calls = []
    original = client.app.state.agent.run

    async def observed(*args, **kwargs):
        calls.append(kwargs)
        async for event in original(*args, **kwargs):
            yield event

    monkeypatch.setattr(client.app.state.agent, "run", observed)
    body = {"message": MAIN, "request_id": "flagged-replay", "search_only": first_flag}
    first = client.post("/api/chat", json=body)
    assert first.status_code == 200
    replay = client.post("/api/chat", json=body | {"retry": True})
    assert replay.status_code == 200 and replay.text == first.text
    conflict = client.post("/api/chat", json=body | {"search_only": not first_flag, "retry": True})
    assert conflict.status_code == 409 and len(calls) == 1
    assert calls[0] == ({"search_only": True} if first_flag else {})


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_search_only_requires_a_json_boolean_before_any_analysis(client, monkeypatch, value):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid search_only must be rejected before analysis")

    monkeypatch.setattr(client.app.state.agent, "run", forbidden)
    assert client.post("/api/chat", json={"message": MAIN, "search_only": value}).status_code == 422


async def test_search_only_ledger_identity_survives_restart_and_preserves_old_false_hash(tmp_path):
    path = tmp_path / "accepted.sqlite3"
    old_hash = hashlib.sha256(b'{"message":"legacy request","session_id":null}').hexdigest()
    assert (
        payload_fingerprint("legacy request", None)
        == payload_fingerprint("legacy request", None, search_only=False)
        == old_hash
    )
    ChatRequestLedger(path).claim("legacy-accepted", old_hash)
    calls = []

    async def run():
        calls.append(True)
        yield AgentEvent(type="done")

    cache = ChatRecovery(ledger_path=path)
    turn = cache.get_or_start("new-search-only", "new request", None, run, search_only=True)
    await turn.task
    await cache.aclose()
    restarted = ChatRecovery(ledger_path=path)
    for request_id, text, flag, expected in (
        ("legacy-accepted", "legacy request", False, 410),
        ("legacy-accepted", "legacy request", True, 409),
        ("new-search-only", "new request", True, 410),
        ("new-search-only", "new request", False, 409),
    ):
        with pytest.raises(ChatRecoveryError) as error:
            restarted.get_or_start(request_id, text, None, run, search_only=flag, retry=True)
        assert error.value.status_code == expected
    assert len(calls) == 1
    await restarted.aclose()
