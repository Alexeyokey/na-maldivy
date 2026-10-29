"""Acceptance for the actual flexible, priced holiday request from the mobile UI.

All quotes are the public offline demo.  The checks deliberately use HTTP and
the configured registry, rather than hand-built packages or a mocked planner.
"""

from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.submission_rehearsal import forbid_external_network
from app.three_destination_rehearsal import mixed_demo_settings
from app.u2_eval import parse_sse

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 29)
WINDOW_END = date(2027, 4, 29)
REQUEST = (
    "Активный отдых у моря на двоих из Москвы до 200 тысяч, "
    "любые даты в ближайшие полгода. Нужны конкретные поездки с ценами"
)
FIRST_MESSAGE = (
    "Хочу активный отдых на море: серфы, дайвинг и проч. Из Москвы на двоих, бюджет чем меньше тем лучше, до 200 тыс."
)


@pytest.fixture
def client(tmp_path):
    settings = mixed_demo_settings(ROOT / "data/demo/aviasales", tmp_path / "checkout.sqlite3")
    with forbid_external_network() as network, TestClient(create_app(settings)) as connected:
        connected.app.state.agent._today = lambda: TODAY
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


def answer(events):
    return "".join(row["data"]["text"] for row in events if row["type"] == "delta")


def assert_priced_holidays(state, events, *, months=None):
    """Every displayed choice is actionable and has an arithmetically real demo quote."""
    brief = state["brief"]
    assert brief["origin"] == "MOW" and brief["adults"] == 2
    assert brief["budget_rub"] == 200_000 and brief["nights"] == 7
    assert "sea" in brief["needs"] and brief["min_water_temperature_c"] >= 24
    assert state["sources"]["nights"] == "default"
    assert state["stage"] == "packages_shown" and not state["stale"]
    assert state["options"]["kind"] == "packages"
    packages = state["options"]["items"]
    assert 2 <= len(packages) <= 3
    assert [row["index"] for row in packages] == list(range(1, len(packages) + 1))
    assert state["selected"] is state["booking"] is None
    assert not any(row["type"] in {"booking", "destination_info"} for row in events)
    assert not any(phrase in answer(events).casefold() for phrase in ("какой месяц", "когда хотите поехать"))
    assert "7" in answer(events) or any(row.get("field") == "nights" for row in state["options"]["assumptions"])
    for package in packages:
        assert package["can_confirm"] and package["validation_status"] == "checked"
        assert package["travelers"] == {"adults": 2, "children_ages": [], "pets": 0}
        assert package["price_scope"] == "round_trip_flights_baggage_hotel_arrival_transfer"
        assert package["party_price_scope"] == "whole_party"
        out, inbound, hotel, transfer = (package[key] for key in ("outbound", "inbound", "hotel", "transfer"))
        assert out["from"] == inbound["to"] == "SVO"
        assert out["to"] == inbound["from"] == package["destination"]
        departure = datetime.fromisoformat(out["departure_at"]).date()
        assert TODAY < departure <= WINDOW_END
        if months is not None:
            assert departure.strftime("%Y-%m") in months
        assert date.fromisoformat(hotel["check_in"]) == datetime.fromisoformat(out["arrival_at"]).date()
        assert date.fromisoformat(hotel["check_out"]) == datetime.fromisoformat(inbound["departure_at"]).date()
        assert (date.fromisoformat(hotel["check_out"]) - date.fromisoformat(hotel["check_in"])).days == 7
        assert hotel["nights"] == 7 and hotel["review_profile"]["hotel_id"] == hotel["hotel_id"]
        assert transfer is not None and transfer["available"] is True
        components = [hotel["price_rub"], transfer["price_rub"]]
        for leg in (out, inbound):
            assert leg["demo_source"]["kind"] in {"deterministic_mock", "provider_replay"}
            assert datetime.fromisoformat(leg["arrival_at"]) > datetime.fromisoformat(leg["departure_at"])
            components.extend((leg["price_rub"], leg["baggage_price_rub"]))
        assert all(isinstance(value, int) and value >= 0 for value in components)
        assert 0 < sum(components) == package["total_rub"] == package["known_total_rub"] <= 200_000
        assert set(package["unpriced_components"]) == {
            "return_transfer",
            "visa_and_other_required_extras",
            "activities_and_equipment",
        }
        activity = package["activity_reference"]
        assert activity["scope"] == "destination_only"
        assert activity["hotel_availability_confirmed"] is activity["included_in_price"] is False
        assert set(activity["requested"]) == set(brief["needs"]) & {"active", "surfing", "diving"}
        assert activity["destination_activities"] and activity["sources"]
        for source in activity["sources"]:
            url = urlsplit(source["url"])
            assert url.scheme == "https" and url.username is url.password is None
            assert url.hostname in {"www.srilanka.travel", "www.indonesia.travel", "thailand.go.th", "www.phuket.go.th"}
            assert date.fromisoformat(source["checked_on"]) <= TODAY
        assert package["is_synthetic"] is True
    return packages


def test_exact_user_request_produces_priced_holidays_without_a_month_question(client):
    state, events = say(client, REQUEST)
    assert state["brief"]["departure_window_start"] == "2026-10-30"
    assert state["brief"]["departure_window_end"] == WINDOW_END.isoformat()
    assert_priced_holidays(state, events)


def test_any_month_follow_up_preserves_the_original_group_budget_and_activities(client):
    before, _ = say(client, FIRST_MESSAGE)
    state, events = say(
        client,
        "Любой месяц, главное по цене чтобы оптимально вышло. Планируй в любой промежуток "
        "в ближайшие полгода, предложи конкретные варианты со стоимостью",
        before["session_id"],
    )
    assert_priced_holidays(state, events)
    assert {"sea", "surfing", "diving"} <= set(state["brief"]["needs"])


def test_four_named_months_are_a_window_not_a_silent_choice_of_january(client):
    before, _ = say(client, FIRST_MESSAGE)
    state, events = say(client, "Ноябрь декабрь январь февраль", before["session_id"])
    months = ["2026-11", "2026-12", "2027-01", "2027-02"]
    assert state["brief"]["departure_months"] == months
    assert state["brief"]["month"] is None
    assert_priced_holidays(state, events, months=months)


def test_a_nonfirst_flexible_option_keeps_its_hotel_route_dates_through_reviews_calendar_and_checkout(client):
    state, events = say(client, REQUEST)
    packages = assert_priced_holidays(state, events)
    chosen = packages[-1]
    sid = state["session_id"]
    unchanged, explained = say(client, f"Почему вариант {chosen['index']} подходит? Что говорят отзывы?", sid)
    assert unchanged == state
    profile = event(explained, "hotel_profile")
    assert profile["status"] == "available" and profile["hotel_id"] == chosen["hotel"]["hotel_id"]
    assert profile["is_synthetic"] and not profile["extraction"]["live_model_run"]
    unchanged, compared = say(client, f"Для варианта {chosen['index']} сравни даты на 1–3 дня позже", sid)
    assert unchanged == state
    facts = event(compared, "date_savings")
    assert facts["baseline"]["hotel_id"] == chosen["hotel"]["hotel_id"]
    assert facts["baseline"]["departure_date"] == chosen["outbound"]["departure_at"][:10]
    assert facts["baseline"]["destination"] == chosen["destination"]
    assert facts["baseline_known_total_rub"] == chosen["total_rub"]
    assert "activities_and_equipment" in facts["unpriced_components"]
    for row in facts["checked_dates"]:
        if row["status"] == "quoted":
            assert row["quote"]["hotel_id"] == chosen["hotel"]["hotel_id"]
            assert row["quote"]["destination"] == chosen["destination"]
    selected, events = say(client, f"Беру вариант {chosen['index']}", sid)
    assert selected["stage"] == "awaiting_confirmation" and selected["booking"] is None
    assert not any(row["type"] == "booking" for row in events)
    assert selected["brief"]["destination"] == chosen["destination"]
    assert selected["brief"]["start_date"] == chosen["outbound"]["departure_at"][:10]
    _, events = say(client, "Подтверждаю состав", sid)
    cart = event(events, "booking")
    details = client.get(f"/api/checkout/carts/{cart['cart_id']}", headers={"X-Session-ID": sid})
    assert details.status_code == 200 and details.json()["package"] == chosen
    assert details.json()["expected_travelers"] == {"adults": 2, "children_ages": [], "pets": 0}
    saved = client.put(
        f"/api/checkout/carts/{cart['cart_id']}/travelers",
        json={
            "session_id": sid,
            "travelers": [
                {"full_name": "SYNTHETIC ADULT ONE", "birth_date": "1990-01-01"},
                {"full_name": "SYNTHETIC ADULT TWO", "birth_date": "1991-01-01"},
            ],
        },
    )
    assert saved.status_code == 200
    paid = client.post(
        f"/api/checkout/carts/{cart['cart_id']}/pay",
        json={
            "session_id": sid,
            "outcome": "approved",
            "idempotency_key": "flexible-journey-demo-only",
            "acknowledge_demo": True,
            "acknowledge_unpriced": True,
        },
    )
    assert paid.status_code == 200 and paid.json()["status"] == "paid_demo"
    assert paid.json()["package"] == chosen
    for key in ("voucher_path", "calendar_path"):
        response = client.get(paid.json()[key], headers={"X-Session-ID": sid})
        assert response.status_code == 200 and response.content
        if key == "voucher_path":
            assert "занятия" in response.text and "аренда" in response.text
            assert "activities_and_equipment" not in response.text
