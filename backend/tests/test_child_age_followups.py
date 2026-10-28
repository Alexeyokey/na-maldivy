"""Rules-only judge flows retain every traveler through pricing and checkout."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.brief import TripBrief, apply_patch
from app.agent.hidden import extract
from app.config import Settings
from app.main import create_app

TODAY = date(2026, 10, 29)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("The default judge flow must not contact a provider or a model")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    app = create_app(
        Settings(
            _env_file=None,
            llm_provider="fake",
            tools_mode="real",
            flights_mode="demo",
            trains_mode="synthetic",
            cache_mode="replay",
            checkout_db_path=tmp_path / "checkout.sqlite3",
            chat_request_db_path=tmp_path / "chat.sqlite3",
        )
    )
    with TestClient(app) as connected:
        app.state.agent._today = lambda: TODAY
        yield connected


def chat(client: TestClient, message: str, session_id: str | None = None):
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


@pytest.mark.parametrize(
    "followup,ages",
    [
        ("5 и 8 лет", [5, 8]),
        ("5, 8 лет", [5, 8]),
        ("5,8 лет", [5, 8]),
        ("Детям 5 лет и 5 лет", [5, 5]),
        ("Двое детей по 5 лет", [5, 5]),
        ("Два ребёнка по 5 лет", [5, 5]),
        ("Дети 5-летний и 8-летний", [5, 8]),
    ],
)
def test_age_followup_prices_and_prepares_checkout_for_both_children(client, followup, ages):
    first, events = chat(client, "Из Москвы в Анталью 18 ноября 2026 на неделю, 2 взрослых с двумя детьми")
    assert any(kind == "question" and value["field"] == "children_ages" for kind, value in events)
    assert first["options"]["items"] == []
    state, _ = chat(client, followup, first["session_id"])
    assert state["brief"]["adults"] == 2
    assert state["brief"]["children_ages"] == ages
    packages = state["options"]["items"]
    assert packages and all(row["travelers"]["children_ages"] == ages for row in packages)

    # Compare the same hotel with a separate one-child search.  A lost sibling
    # must not silently produce the cheaper one-child quote.
    single, _ = chat(client, f"Из Москвы в Анталью 18 ноября 2026 на неделю, 2 взрослых, ребёнку {ages[0]} лет")
    one_child = {row["hotel"]["hotel_id"]: row for row in single["options"]["items"]}
    chosen = next(row for row in packages if row["can_confirm"] and row["hotel"]["hotel_id"] in one_child)
    assert chosen["known_total_rub"] > one_child[chosen["hotel"]["hotel_id"]]["known_total_rub"]

    selected, _ = chat(client, f"Беру вариант {chosen['index']}", first["session_id"])
    assert selected["stage"] == "awaiting_confirmation"
    confirmed, _ = chat(client, "Подтверждаю", first["session_id"])
    cart_response = client.get(
        f"/api/checkout/carts/{confirmed['booking']['cart_id']}",
        headers={"X-Session-ID": first["session_id"]},
    )
    assert cart_response.status_code == 200
    cart = cart_response.json()
    assert cart["expected_travelers"] == {"adults": 2, "children_ages": ages, "pets": 0}
    assert cart["package"]["travelers"]["children_ages"] == ages
    assert cart["package"]["known_total_rub"] == chosen["known_total_rub"]


def test_age_limits_and_unrelated_numbers_never_create_a_different_family():
    context = TripBrief(needs=["child"])
    for text in (
        "2 взрослых, дата 18 ноября 2026, на 7 ночей",
        "Перенеси на 2 дня позже, через 5 лет не планируем",
        "До 2 лет",
    ):
        assert extract(text, TODAY, context).children_ages is None
    assert extract("Мне 17 лет, в 2027 году хочу поездку на 5 лет", TODAY).children_ages is None
    assert extract("Детям 0, 5 и 17 лет", TODAY).children_ages == [0, 5, 17]
    too_many = extract("9 детей по 5 лет", TODAY)
    applied = apply_patch(TripBrief(), {}, too_many, today=TODAY)
    assert applied.brief.children_ages == []
    assert any(row.field == "children_ages" for row in applied.rejected)
    invalid_ages = apply_patch(context, {}, extract("Детям 18 и 5 лет", TODAY, context), today=TODAY)
    assert invalid_ages.brief.children_ages == []
    assert any(row.field == "children_ages" for row in invalid_ages.rejected)
    assert extract("Мне 35 лет, детям 5 и 8 лет", TODAY, context).children_ages == [5, 8]
    assert extract("2 взрослых: 35 и 36 лет, детям 5 и 8 лет", TODAY, context).children_ages == [5, 8]
    removed = apply_patch(
        context.model_copy(update={"children_ages": [5, 5]}), {}, extract("Без детей", TODAY, context), today=TODAY
    )
    assert removed.brief.children_ages == [] and "child" not in removed.brief.needs


def test_counted_children_followup_preserves_the_explicit_single_adult(client):
    before, _ = chat(client, "Из Москвы в Анталью 18 ноября 2026 на неделю, 1 взрослый с детьми")
    after, _ = chat(client, "Двое детей по 5 лет", before["session_id"])
    assert after["brief"]["adults"] == 1 and after["sources"]["adults"] == "explicit"
    assert after["brief"]["children_ages"] == [5, 5]
    assert all(option["travelers"]["adults"] == 1 for option in after["options"]["items"])


@pytest.mark.parametrize(
    "answer,origin",
    [("Москва", "MOW"), ("MOW", "MOW"), ("Питер", "LED"), ("Санкт-Петербург", "LED"), ("Шереметьево", "SVO")],
)
def test_bare_city_answers_the_origin_question_without_replacing_the_destination(client, answer, origin):
    before, events = chat(client, "Хочу в Анталью 18 ноября 2026 на неделю")
    assert any(kind == "question" and value["field"] == "origin" for kind, value in events)
    after, _ = chat(client, answer, before["session_id"])
    assert (after["brief"]["origin"], after["brief"]["destination"], after["brief"]["transport"]) == (
        origin,
        "AYT",
        "flight",
    )
    assert after["stage"] == "packages_shown"
    assert after["options"]["items"]


@pytest.mark.parametrize("answer", ["Москва", "Шереметьево", "SVO"])
def test_bare_train_origin_normalizes_airport_to_the_departure_city(client, answer):
    before, _ = chat(client, "Хочу поезд в Петербург 18 ноября 2026")
    after, _ = chat(client, answer, before["session_id"])
    assert (after["brief"]["origin"], after["brief"]["destination"], after["brief"]["transport"]) == (
        "MOW",
        "LED",
        "train",
    )
    assert after["options"]["kind"] == "trains" and after["options"]["items"]


@pytest.mark.parametrize("answer,origin", [("Москва", "MOW"), ("Питер", "LED"), ("Шереметьево", "SVO")])
def test_bare_origin_without_a_destination_continues_to_destination_suggestions(client, answer, origin):
    before, events = chat(client, "Хочу отдохнуть в ноябре")
    assert before["brief"]["destination"] is None
    assert any(kind == "question" and value["field"] == "origin" for kind, value in events)
    after, _ = chat(client, answer, before["session_id"])
    assert (after["brief"]["origin"], after["brief"]["destination"], after["brief"]["transport"]) == (
        origin,
        None,
        "flight",
    )
    assert after["stage"] == "destinations_shown" and after["options"]["items"]


@pytest.mark.parametrize("message", ["В Москву", "Хочу в Питер", "А Москва красивый город?"])
def test_explicit_destination_and_unrelated_city_mentions_are_not_origin_answers(message):
    patch = extract(message, TODAY, TripBrief(destination="AYT", month=11))
    assert patch.origin is None
    if message == "В Москву":
        assert patch.destination == "MOW" and patch.transport == "train"
    elif message == "Хочу в Питер":
        assert patch.destination == "LED" and patch.transport == "train"


def test_exact_infant_quick_reply_uses_an_age_below_two(client):
    before, events = chat(client, "Из Москвы в Анталью 18 ноября 2026 на неделю, 2 взрослых с ребёнком")
    question = next(value for kind, value in events if kind == "question")
    assert "1 год" in question["quick_replies"] and "До 2 лет" not in question["quick_replies"]
    after, _ = chat(client, "1 год", before["session_id"])
    assert after["brief"]["children_ages"] == [1]
    assert after["options"]["items"]
    assert all(option["travelers"]["children_ages"] == [1] for option in after["options"]["items"])
