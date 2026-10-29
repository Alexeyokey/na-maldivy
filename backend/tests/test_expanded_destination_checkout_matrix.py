"""Every advertised D5 destination can complete the actual demo checkout API.

The existing expanded-data tests cover search and representative restart paths.
This matrix closes the per-destination gap from an actual suggested card to both
downloaded documents. Network and external models are deliberately unavailable.
"""

import hashlib
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.submission_rehearsal import forbid_external_network
from app.three_destination_rehearsal import mixed_demo_settings
from app.tools.destinations import load_catalogue
from app.u2_eval import parse_sse

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = load_catalogue(ROOT / "data/destinations.yaml")


@pytest.mark.parametrize("record", CATALOGUE.destinations, ids=lambda row: row.iata)
def test_each_suggested_destination_reaches_payment_and_both_documents(tmp_path, record):
    settings = mixed_demo_settings(ROOT / "data/demo/aviasales", tmp_path / "checkout.sqlite3")
    with forbid_external_network() as network, TestClient(create_app(settings)) as client:
        client.app.state.agent._today = lambda: date(2026, 10, 29)
        destination_tool = client.app.state.agent._tools["suggest_destinations"]
        destination_tool.catalogue = CATALOGUE.model_copy(update={"destinations": (record,)})

        def say(text, sid=None):
            response = client.post("/api/chat", json={"message": text, **({"session_id": sid} if sid else {})})
            assert response.status_code == 200
            events = parse_sse(response.text)
            assert events[-1]["type"] == "done" and not any(row["type"] == "error" for row in events)
            state = events[-1]["data"]
            assert client.get(f"/api/session/{state['session_id']}").json() == state
            return state, events

        suggested, _ = say("Из Москвы 18 ноября 2026, 2 взрослых, 7 ночей. Посоветуй направление")
        assert suggested["options"]["kind"] == "destinations"
        cards = suggested["options"]["items"]
        assert len(cards) == 1 and cards[0]["code"] == record.iata and cards[0]["full_trip_supported"] is True
        sid = suggested["session_id"]
        prompt = f"Хочу в {record.iata}" + (", самолётом" if record.iata == "LED" else "")
        state, _ = say(prompt, sid)
        assert state["brief"]["destination"] == record.iata and state["brief"]["transport"] == "flight"
        assert state["options"]["kind"] == "packages" and state["selected"] is state["booking"] is None
        safe = next(row for row in state["options"]["items"] if row["can_confirm"])
        assert safe["destination"] == record.iata
        for option in state["options"]["items"]:
            assert {
                "check_in_time",
                "check_out_time",
                "timezone",
                "reception_24h",
                "reception_closes_at",
                "early_check_in_available",
                "early_check_in_price_rub",
                "adults_only",
                "pets_allowed",
            } <= option["hotel"].keys()

        unchanged, explained = say(client_text := f"Почему вариант {safe['index']} подходит? Что говорят отзывы?", sid)
        assert unchanged == state, client_text
        profile = next(row["data"] for row in explained if row["type"] == "hotel_profile")
        assert profile["status"] == "available" and profile["hotel_id"] == safe["hotel"]["hotel_id"]
        assert profile["is_synthetic"] and profile["evidence"] and not profile["extraction"]["live_model_run"]
        unchanged, compared = say(f"Для варианта {safe['index']} сравни даты на 1–3 дня раньше", sid)
        assert unchanged == state
        comparison = next(row["data"] for row in compared if row["type"] == "date_savings")
        assert comparison["baseline"]["hotel_id"] == safe["hotel"]["hotel_id"]
        assert any(row["status"] == "quoted" for row in comparison["checked_dates"])

        updated, _ = say("Только возвратный отель, остальные условия сохраняем, без оформления.", sid)
        assert updated["brief"]["destination"] == record.iata
        assert updated["brief"]["start_date"] == state["brief"]["start_date"]
        assert updated["brief"]["adults"] == 2 and updated["brief"]["refundable_only"] is True
        assert updated["selected"] is updated["booking"] is None
        assert updated["options"]["items"] and all(
            row["hotel"]["refundable"] is True for row in updated["options"]["items"]
        )
        shifted, _ = say("На два дня позже", sid)
        assert shifted["brief"]["start_date"] == "2026-11-20"
        assert shifted["brief"]["destination"] == record.iata and shifted["brief"]["refundable_only"] is True
        assert shifted["selected"] is shifted["booking"] is None
        safe = next(row for row in shifted["options"]["items"] if row["can_confirm"])
        say(f"Беру вариант {safe['index']}", sid)
        _, confirmation = say("Подтверждаю состав", sid)
        cart = next(row["data"]["cart_id"] for row in confirmation if row["type"] == "booking")
        passengers = [
            {"full_name": "SYNTHETIC ADULT ONE", "birth_date": "1990-01-01"},
            {"full_name": "SYNTHETIC ADULT TWO", "birth_date": "1991-01-01"},
        ]
        saved = client.put(f"/api/checkout/carts/{cart}/travelers", json={"session_id": sid, "travelers": passengers})
        assert saved.status_code == 200
        payment = {
            "session_id": sid,
            "acknowledge_demo": True,
            "acknowledge_unpriced": True,
        }
        declined = client.post(
            f"/api/checkout/carts/{cart}/pay",
            json={**payment, "outcome": "declined", "idempotency_key": "full-catalogue-declined-" + record.iata},
        )
        assert declined.status_code == 200 and declined.json()["status"] == "payment_declined"
        paid = client.post(
            f"/api/checkout/carts/{cart}/pay",
            json={**payment, "outcome": "approved", "idempotency_key": "full-catalogue-checkout-" + record.iata},
        )
        assert paid.status_code == 200 and paid.json()["status"] == "paid_demo"
        for key in ("voucher_path", "calendar_path"):
            path = paid.json()[key]
            downloaded = client.get(path, headers={"X-Session-ID": sid})
            assert downloaded.status_code == 200 and downloaded.content
            repeated = client.get(path, headers={"X-Session-ID": sid})
            assert repeated.status_code == 200
            assert hashlib.sha256(repeated.content).digest() == hashlib.sha256(downloaded.content).digest()
        assert network["blocked_attempts"] == 0
