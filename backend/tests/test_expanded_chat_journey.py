"""Use generated data through chat, reviews, date comparison and durable checkout."""

import hashlib
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.submission_rehearsal import forbid_external_network
from app.three_destination_rehearsal import mixed_demo_settings
from app.tools.destinations import load_catalogue
from app.u2_eval import parse_sse

ROOT = Path(__file__).resolve().parents[2]
RECORDS = load_catalogue(ROOT / "data/destinations.yaml").destinations
PASSENGERS = [
    {"full_name": "SYNTHETIC ADULT ONE", "birth_date": "1990-01-01"},
    {"full_name": "SYNTHETIC ADULT TWO", "birth_date": "1991-01-01"},
]


@pytest.fixture
def client(tmp_path):
    settings = mixed_demo_settings(ROOT / "data/demo/aviasales", tmp_path / "checkout.sqlite3")
    with forbid_external_network() as network, TestClient(create_app(settings)) as connected:
        connected.app.state.agent._today = lambda: date(2026, 10, 29)
        connected.demo_settings = settings
        yield connected
        assert network["blocked_attempts"] == 0


def say(client, text, sid=None):
    response = client.post("/api/chat", json={"message": text, **({"session_id": sid} if sid else {})})
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1]["type"] == "done" and not any(row["type"] == "error" for row in events)
    state = events[-1]["data"]
    assert client.get(f"/api/session/{state['session_id']}").json() == state
    return state, events


def event(events, kind):
    return next(row["data"] for row in events if row["type"] == kind)


@pytest.mark.parametrize("record", RECORDS, ids=lambda row: row.iata)
def test_every_advertised_destination_has_a_real_chat_to_demo_package_path(client, record):
    state, events = say(client, f"Из Москвы в {record.iata} 18 ноября 2026, 2 взрослых, 7 ночей, самолётом")
    assert state["brief"]["destination"] == record.iata and state["brief"]["transport"] == "flight"
    assert state["stage"] == "packages_shown" and not state["stale"]
    assert state["options"]["kind"] == "packages" and state["options"]["items"]
    assert any(row["can_confirm"] for row in state["options"]["items"])
    assert state["selected"] is state["booking"] is None
    assert not any(row["type"] in {"booking", "destination_info"} for row in events)
    for package in state["options"]["items"]:
        assert package["outbound"]["to"] == package["inbound"]["from"] == record.iata
        assert package["transfer"]["price_rub"] >= 0
        assert package["hotel"]["review_profile"]["hotel_id"] == package["hotel"]["hotel_id"]
        if record.iata not in {"AYT", "DXB", "MLE"}:
            assert package["hotel"]["hotel_id"].startswith(f"demo:hotel/{record.iata}-")
            assert package["hotel"]["name"].startswith("Демо")
            assert all(package[leg]["demo_source"]["kind"] == "deterministic_mock" for leg in ("outbound", "inbound"))


def test_real_suggested_sri_lanka_card_uses_the_generated_trip_not_a_reference_dead_end(client):
    before, _ = say(client, "Куда поехать из Москвы в январе 2027, 2 взрослых, 7 ночей, хочу тепло и море")
    option = next(row for row in before["options"]["items"] if row["code"] == "CMB")
    assert option["full_trip_supported"] is True
    after, _ = say(client, "Хочу в CMB", before["session_id"])
    assert after["brief"]["destination"] == "CMB"
    assert after["options"]["kind"] == "packages" and any(row["can_confirm"] for row in after["options"]["items"])


@pytest.mark.parametrize(
    "destination,day",
    [
        ("CMB", "2026-11-18"),
        ("HKT", "2027-01-15"),
        ("KZN", "2027-01-15"),
        ("NRT", "2027-01-15"),
        ("PKC", "2027-01-15"),
        ("BEG", "2027-03-25"),
    ],
)
def test_new_destination_reviews_calendar_demo_payment_and_documents_survive_restart(client, destination, day):
    state, _ = say(client, f"Из Москвы в {destination} {day}, 2 взрослых, 7 ночей, самолётом")
    sid = state["session_id"]
    safe = next(row for row in state["options"]["items"] if row["can_confirm"])
    unchanged, explained = say(client, f"Почему вариант {safe['index']} подходит? Что говорят отзывы?", sid)
    assert unchanged == state
    profile = event(explained, "hotel_profile")
    assert profile["status"] == "available" and profile["hotel_id"] == safe["hotel"]["hotel_id"]
    assert profile["is_synthetic"] and not profile["extraction"]["live_model_run"] and profile["evidence"]
    unchanged, compared = say(client, f"Для варианта {safe['index']} сравни даты на 1–3 дня раньше", sid)
    assert unchanged == state
    facts = event(compared, "date_savings")
    assert facts["baseline"]["hotel_id"] == safe["hotel"]["hotel_id"] and facts["checked_dates"]
    assert any(row["status"] == "quoted" for row in facts["checked_dates"])
    for row in facts["checked_dates"]:
        if row["status"] == "quoted":
            assert row["quote"]["hotel_id"] == safe["hotel"]["hotel_id"]
    say(client, f"Беру вариант {safe['index']}", sid)
    _, confirmed = say(client, "Подтверждаю состав", sid)
    cart = event(confirmed, "booking")["cart_id"]
    result = client.put(f"/api/checkout/carts/{cart}/travelers", json={"session_id": sid, "travelers": PASSENGERS})
    assert result.status_code == 200
    common = {"session_id": sid, "acknowledge_demo": True, "acknowledge_unpriced": True}
    declined = client.post(
        f"/api/checkout/carts/{cart}/pay",
        json={**common, "outcome": "declined", "idempotency_key": "expanded-declined"},
    )
    assert declined.status_code == 200 and declined.json()["status"] == "payment_declined"
    paid = client.post(
        f"/api/checkout/carts/{cart}/pay",
        json={**common, "outcome": "approved", "idempotency_key": "expanded-approved"},
    )
    assert paid.status_code == 200 and paid.json()["status"] == "paid_demo"
    order = paid.json()
    headers = {"X-Session-ID": sid}
    fingerprints = {}
    for key in ("voucher_path", "calendar_path"):
        response = client.get(order[key], headers=headers)
        assert response.status_code == 200
        fingerprints[key] = hashlib.sha256(response.content).hexdigest()
    for leg in (safe["outbound"], safe["inbound"]):
        assert datetime.fromisoformat(leg["arrival_at"]) > datetime.fromisoformat(leg["departure_at"])
    with TestClient(create_app(client.demo_settings)) as restarted:
        assert restarted.get(f"/api/session/{sid}").status_code == 404
        restored = restarted.get(f"/api/checkout/orders/{order['order_id']}", headers=headers)
        assert restored.status_code == 200 and restored.json() == order
        for key, expected in fingerprints.items():
            assert hashlib.sha256(restarted.get(order[key], headers=headers).content).hexdigest() == expected
