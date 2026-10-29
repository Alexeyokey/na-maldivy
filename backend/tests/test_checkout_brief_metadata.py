"""Identical search criteria keep their checkout revision and immutable orders."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import FakeLLMClient, LLMResponse
from app.main import create_app


@pytest.fixture
def running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Metadata and checkout checks must not use a provider or a real model")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    app = create_app(
        Settings(
            _env_file=None,
            llm_provider="fake",
            tools_mode="real",
            flights_mode="demo",
            cache_mode="replay",
            checkout_db_path=tmp_path / "checkout.sqlite3",
            chat_request_db_path=tmp_path / "chat.sqlite3",
        )
    )
    with TestClient(app) as client:
        app.state.agent._today = lambda: date(2026, 10, 29)
        yield client, app


def chat(client, message, session_id=None):
    payload = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    events = []
    for block in response.text.replace("\r\n", "\n").split("\n\n"):
        lines = block.splitlines()
        kind = next((line[7:] for line in lines if line.startswith("event: ")), None)
        body = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if kind and body:
            events.append((kind, json.loads(body)))
    assert events and events[-1][0] == "done", events
    assert not any(kind == "error" for kind, _ in events)
    return events[-1][1], events


def begin_checkout(client):
    searched, _ = chat(client, "Из Москвы в Анталью 18 ноября 2026")
    session_id = searched["session_id"]
    chosen = next(row for row in searched["options"]["items"] if row["can_confirm"])
    selected, _ = chat(client, f"Беру вариант {chosen['index']}", session_id)
    assert selected["stage"] == "awaiting_confirmation"
    pending, _ = chat(client, "Подтверждаю", session_id)
    assert pending["stage"] == "checkout_pending"
    return pending, pending["booking"]["cart_id"]


def travelers(client, session_id, cart_id):
    response = client.put(
        f"/api/checkout/carts/{cart_id}/travelers",
        json={"session_id": session_id, "travelers": [{"full_name": "Demo Adult", "birth_date": "1990-01-01"}]},
    )
    assert response.status_code == 200
    return response.json()


def payment(client, session_id, cart_id, outcome, key):
    return client.post(
        f"/api/checkout/carts/{cart_id}/pay",
        json={
            "session_id": session_id,
            "outcome": outcome,
            "idempotency_key": key,
            "acknowledge_demo": True,
            "acknowledge_unpriced": True,
        },
    )


def documents(client, session_id, order_id):
    result = []
    for suffix in ("voucher", "calendar.ics"):
        response = client.get(f"/api/checkout/orders/{order_id}/{suffix}", headers={"X-Session-ID": session_id})
        assert response.status_code == 200
        result.append(response.content)
    return result


def test_source_only_confirmation_keeps_pending_cart_then_paid_documents_and_replay(running):
    client, _ = running
    before, cart_id = begin_checkout(client)
    sid, version, selected = before["session_id"], before["version"], before["selected"]
    original_cart = travelers(client, sid, cart_id)
    declined = payment(client, sid, cart_id, "declined", "metadata-declined-first")
    assert declined.status_code == 200 and declined.json()["status"] == "payment_declined"

    after, events = chat(client, "Едет 1 взрослый на 7 ночей", sid)
    assert after["sources"]["adults"] == after["sources"]["nights"] == "explicit"
    assert (after["version"], after["selected"], after["stage"]) == (version, selected, "checkout_pending")
    assert after["booking"]["cart_id"] == cart_id
    assert after["options"] == before["options"]
    assert not any(kind in {"options", "booking", "tool_call"} for kind, _ in events)
    same_cart = client.get(f"/api/checkout/carts/{cart_id}", headers={"X-Session-ID": sid}).json()
    assert same_cart["travelers"] == original_cart["travelers"]
    assert same_cart["package"] == original_cart["package"]

    approved = payment(client, sid, cart_id, "approved", "metadata-approved-second")
    assert approved.status_code == 200 and approved.json()["status"] == "paid_demo"
    order = approved.json()
    saved_documents = documents(client, sid, order["order_id"])
    paid, _ = chat(client, "В ноябре 2026", sid)
    assert paid["sources"]["month"] == paid["sources"]["year"] == "explicit"
    assert (paid["version"], paid["selected"], paid["stage"]) == (version, selected, "booked")
    assert documents(client, sid, order["order_id"]) == saved_documents
    assert payment(client, sid, cart_id, "approved", "metadata-approved-second").json() == order

    changed, _ = chat(client, "Теперь едут 2 взрослых", sid)
    assert changed["version"] > version and changed["selected"] is changed["booking"] is None
    assert payment(client, sid, cart_id, "approved", "new-key-old-cart").status_code == 409
    assert documents(client, sid, order["order_id"]) == saved_documents


def test_narrative_mood_change_does_not_reprice_or_expire_the_pending_checkout(running):
    client, app = running
    before, cart_id = begin_checkout(client)
    sid = before["session_id"]
    app.state.agent._llm = FakeLLMClient(
        [
            LLMResponse(text=json.dumps({"intent": "modify", "patch": {"mood": "мечтательный"}}), model="controlled"),
            LLMResponse(text="Пожелание записано, состав поездки прежний.", model="controlled"),
        ]
    )
    app.state.agent._llm_writes = True
    after, events = chat(client, "Настроение мечтательное", sid)
    assert after["brief"]["mood"] == "мечтательный"
    assert (after["version"], after["selected"], after["stage"]) == (
        before["version"],
        before["selected"],
        "checkout_pending",
    )
    assert after["options"] == before["options"] and after["booking"]["cart_id"] == cart_id
    assert not any(kind in {"options", "booking", "tool_call"} for kind, _ in events)
    travelers(client, sid, cart_id)
    approved = payment(client, sid, cart_id, "approved", "mood-checkout-approved")
    assert approved.status_code == 200 and approved.json()["status"] == "paid_demo"
