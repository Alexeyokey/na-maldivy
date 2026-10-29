"""A destination suggested by the agent must remain usable after its card click."""

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


@pytest.fixture
def client(tmp_path):
    settings = mixed_demo_settings(ROOT / "data/demo/aviasales", tmp_path / "checkout.sqlite3")
    # Strict finite synthetic mode intentionally lacks the opt-in demo expansion.
    # The expanded demo journey is tested separately in test_expanded_chat_journey.
    settings.flights_mode = "synthetic"
    with forbid_external_network() as network, TestClient(create_app(settings)) as connected:
        connected.app.state.agent._today = lambda: date(2026, 10, 29)
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


def answer(events):
    return "".join(row["data"]["text"] for row in events if row["type"] == "delta")


@pytest.mark.parametrize(
    "prompt", ["Хочу в Шри-Ланка", "Хочу в Шри-Ланку", "Хочу в Sri Lanka", "Расскажи о направлении CMB"]
)
def test_old_and_new_sri_lanka_card_prompts_return_reference_facts_without_fake_search(client, prompt):
    before, _ = say(client, "Из Москвы в Анталью 18 ноября 2026, 2 взрослых, 7 ночей")
    after, events = say(client, prompt, before["session_id"])
    assert after["brief"]["destination"] == "CMB"
    for key in ("origin", "month", "year", "start_date", "nights", "adults", "children_ages", "pets"):
        assert after["brief"][key] == before["brief"][key]
    assert after["stage"] == "destinations_shown" and not after["stale"]
    assert not after["options"]["items"] and after["selected"] is after["booking"] is None
    assert "Шри-Ланка (CMB)" in answer(events) and "справочное направление" in answer(events)
    assert "Не смог распознать" not in answer(events)
    assert not any(row["type"] in {"tool_call", "booking"} for row in events)
    facts = next(row["data"] for row in events if row["type"] == "destination_info")
    assert facts["destination"] == "CMB" and facts["full_trip_supported"] is False
    assert facts["full_trip_price_rub"] is None and facts["availability_confirmed"] is False
    recovered, _ = say(client, "Анталья", after["session_id"])
    assert recovered["brief"]["destination"] == "AYT" and recovered["options"]["items"]


def test_actual_suggestion_to_reference_card_then_supported_trip(client):
    tool = client.app.state.agent._tools["suggest_destinations"]
    catalogue = load_catalogue(ROOT / "data/destinations.yaml")
    tool.catalogue = catalogue.model_copy(
        update={"destinations": tuple(row for row in catalogue.destinations if row.iata == "CMB")}
    )
    before, _ = say(client, "Куда поехать из Москвы в январе 2027, 2 взрослых, 7 ночей?")
    cards = before["options"]["items"]
    assert before["options"]["kind"] == "destinations" and len(cards) == 1
    assert cards[0]["code"] == "CMB" and cards[0]["full_trip_supported"] is False
    after, events = say(client, f"Расскажи о направлении {cards[0]['code']}", before["session_id"])
    assert after["brief"]["destination"] == "CMB" and "среднемноголетние" in answer(events)
    assert "Не смог распознать" not in answer(events)
    tool.catalogue = None
    recovered, _ = say(client, "Хочу в AYT", after["session_id"])
    assert recovered["options"]["kind"] == "packages" and recovered["options"]["items"]
    assert recovered["brief"]["adults"] == 2 and recovered["brief"]["month"] == 1


def test_supported_cards_are_marked_as_searchable_without_claiming_inventory(client):
    tool = client.app.state.agent._tools["suggest_destinations"]
    catalogue = load_catalogue(ROOT / "data/destinations.yaml")
    tool.catalogue = catalogue.model_copy(
        update={"destinations": tuple(row for row in catalogue.destinations if row.iata in {"AYT", "DXB", "MLE"})}
    )
    before, _ = say(client, "Куда поехать из Москвы в ноябре 2026 на 7 ночей?")
    assert before["options"]["items"]
    assert all(row["full_trip_supported"] is True for row in before["options"]["items"])
    after, _ = say(client, f"Хочу в {before['options']['items'][0]['code']}", before["session_id"])
    assert after["options"]["kind"] == "packages" and after["options"]["items"]
    assert after["selected"] is after["booking"] is None


@pytest.mark.parametrize(
    "record", load_catalogue(ROOT / "data/destinations.yaml").destinations, ids=lambda row: row.iata
)
def test_every_catalogue_card_name_is_resolved_in_the_actual_chat(client, record):
    state, events = say(client, f"Хочу в {record.name}")
    assert state["brief"]["destination"] == record.iata
    assert "Не смог распознать" not in answer(events)
    if record.iata not in {"AYT", "DXB", "MLE", "LED"}:
        facts = next(row["data"] for row in events if row["type"] == "destination_info")
        assert facts["destination"] == record.iata and facts["full_trip_supported"] is False
        assert not any(row["type"] == "tool_call" for row in events)


def test_reference_lookup_still_explains_rejected_search_conditions(client):
    state, events = say(client, "Хочу в CMB 18 ноября 2026, 0 взрослых, на 7 ночей")
    assert state["brief"]["destination"] == "CMB" and state["brief"]["adults"] == 1
    assert "Не смог принять" in answer(events)


@pytest.mark.parametrize("correction", ["точнее", "вернее", "но лучше"])
def test_explicit_destination_correction_uses_the_final_target(client, correction):
    state, events = say(client, f"Хочу в Шри-Ланку, {correction} в Дубай")
    assert state["brief"]["destination"] == "DXB"
    assert "Не смог распознать" not in answer(events)
    assert not any(row["type"] == "destination_info" for row in events)


def test_reference_card_command_with_question_mark_does_not_answer_about_the_previous_route(client):
    before, _ = say(client, "Хочу в BKK")
    state, events = say(client, "Расскажи о направлении CMB?", before["session_id"])
    assert state["brief"]["destination"] == "CMB"
    facts = next(row["data"] for row in events if row["type"] == "destination_info")
    assert facts["destination"] == "CMB"
