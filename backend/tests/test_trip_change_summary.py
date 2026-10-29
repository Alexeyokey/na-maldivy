"""HTTP regressions for exact UI refinements and authoritative, bounded comparisons."""

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.brief import TripBrief, apply_patch
from app.agent.hidden import NEEDS, extract
from app.agent.trip_changes import build_diff, option_summary
from app.main import create_app
from app.submission_rehearsal import forbid_external_network
from app.three_destination_rehearsal import mixed_demo_settings
from app.u2_eval import parse_sse

ROOT = Path(__file__).resolve().parents[2]
MAIN = "Из Москвы в Анталью 18 ноября 2026 на неделю, 2 взрослых, бюджет 350 тысяч"
QUICK = [
    ("Подбери более дешёвые варианты на тех же условиях, без оформления", "needs", "budget_low", True),
    ("Только прямые рейсы, остальные условия сохраняем, без оформления", "direct_only", True, True),
    ("Только возвратный отель, остальные условия сохраняем, без оформления", "refundable_only", True, True),
    ("Можно с пересадками, остальные условия сохраняем, без оформления", "direct_only", False, False),
    ("Можно и невозвратные отели, остальные условия сохраняем, без оформления", "refundable_only", False, False),
    ("Сними приоритет низкой цены, остальные условия сохраняем, без оформления", "needs", "budget_low", False),
]


@pytest.fixture
def client(tmp_path):
    settings = mixed_demo_settings(ROOT / "data/demo/aviasales", tmp_path / "checkout.sqlite3")
    with forbid_external_network() as network, TestClient(create_app(settings)) as connected:
        connected.app.state.agent._today = lambda: date(2026, 10, 29)
        yield connected
        assert network["blocked_attempts"] == 0


def say(client, message, sid=None):
    response = client.post("/api/chat", json={"message": message, **({"session_id": sid} if sid else {})})
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1]["type"] == "done" and not any(row["type"] == "error" for row in events)
    state = events[-1]["data"]
    assert client.get(f"/api/session/{state['session_id']}").json() == state
    return state, events


def diff(state, events):
    result = next(row["data"] for row in events if row["type"] == "diff")
    assert result == state["last_diff"] and result["version"] == state["version"] and result["schema_version"] == 1
    assert result["comparison"]["savings_guaranteed"] is False
    forbidden = {"id", "session_id", "cart_id", "order_id", "full_name", "birth_date", "hotel_id", "offer_id"}

    def inspect(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(result)
    return result


@pytest.mark.parametrize("message,field,value,enable", QUICK)
@pytest.mark.parametrize("selected", [False, True])
def test_exact_quick_refinements_preserve_the_trip_and_never_choose_or_book(
    client, message, field, value, enable, selected
):
    request = MAIN + ", с ребёнком 5 лет и собакой"
    if not enable:
        request += ", только прямые рейсы, возвратный отель, недорого"
    before, _ = say(client, request)
    assert before["last_diff"] is None
    sid = before["session_id"]
    if selected:
        chosen = next(row for row in before["options"]["items"] if row["can_confirm"])
        before, _ = say(client, f"Беру вариант {chosen['index']}", sid)
    after, events = say(client, message + ".", sid)
    retained = (
        "origin",
        "destination",
        "start_date",
        "month",
        "year",
        "nights",
        "adults",
        "children_ages",
        "pets",
        "budget_rub",
    )
    assert {key: after["brief"][key] for key in retained} == {key: before["brief"][key] for key in retained}
    if field == "needs":
        assert (value in after["brief"][field]) is enable
    else:
        assert after["brief"][field] is value
    assert after["selected"] is after["booking"] is None
    assert not any(row["type"] == "booking" for row in events)
    assert any(row["type"] == "tool_call" and row["data"]["name"] == "search_flights" for row in events)
    result = diff(after, events)
    assert all(change["field"] not in retained for change in result["criteria_changes"])
    assert result["before"]["travelers"] == result["after"]["travelers"]
    if result["comparison"]["delta_known_total_rub"] == 0:
        assert "Известная сумма не изменилась" in "".join(
            row["data"]["text"] for row in events if row["type"] == "delta"
        )
    # A repeated filter is a no-op: it must not erase a previous explanation or turn into confirmation.
    again, events = say(client, message, sid)
    assert again == after and not any(row["type"] in {"booking", "diff"} for row in events)


def test_change_summary_matches_actual_options_components_and_group_difference(client):
    before, _ = say(client, MAIN)
    sid = before["session_id"]
    after, events = say(client, "Теперь 20 ноября 2026 на 5 ночей, трое взрослых", sid)
    result = diff(after, events)
    assert result["status"] == "updated"
    for side, state in (("before", before), ("after", after)):
        summary, first = result[side], state["options"]["items"][0]
        assert summary["hotel"] == summary["name"] == first["hotel"]["name"]
        assert summary["known_total_rub"] == first["known_total_rub"]
        assert summary["travelers"] == first["travelers"]
        assert sum(row["known_cost_rub"] for row in summary["components"]) == summary["known_total_rub"]
        assert summary["unpriced_components"] == first["unpriced_components"]
    assert {"dates", "travelers"} <= set(result["comparison"]["differences"])
    assert not result["comparison"]["same_basis"]
    assert (
        result["comparison"]["delta_known_total_rub"]
        == after["options"]["items"][0]["known_total_rub"] - before["options"]["items"][0]["known_total_rub"]
    )
    change = {row["field"]: row for row in result["criteria_changes"]}
    assert (change["adults"]["before"], change["adults"]["after"]) == (2, 3)
    brief_event = next(row["data"] for row in events if row["type"] == "brief" and row["data"]["search_changed"])
    assert brief_event["last_diff"] is None


def test_no_results_and_provider_failure_replace_old_comparisons_without_zero_costs(client, monkeypatch):
    state, _ = say(client, MAIN)
    sid = state["session_id"]
    state, events = say(client, "Теперь 20 ноября 2026", sid)
    assert diff(state, events)["after"]
    empty, events = say(client, "Бюджет 1 рубль", sid)
    result = diff(empty, events)
    assert result["status"] == "no_results" and result["after"] is None
    assert result["comparison"]["delta_known_total_rub"] is None
    assert empty["options"]["items"] == []
    recovered, events = say(client, "Сними бюджет", sid)
    result = diff(recovered, events)
    assert result["before"] is None and result["after"] and result["comparison"]["delta_known_total_rub"] is None

    async def fail(_):
        raise ValueError("source is unavailable")

    monkeypatch.setattr(client.app.state.agent._tools["search_flights"], "run", fail)
    unavailable, events = say(client, "Теперь 22 ноября 2026", sid)
    result = diff(unavailable, events)
    assert result["status"] == "no_results" and result["after"] is None and result["notes"]
    assert result["comparison"]["delta_known_total_rub"] is None


def test_pending_age_clarification_keeps_only_an_honest_before_and_reset_clears_everything(client):
    state, _ = say(client, MAIN)
    sid = state["session_id"]
    pending, events = say(client, "Теперь с ребёнком", sid)
    result = diff(pending, events)
    assert result["status"] == "awaiting_input" and result["after"] is None
    assert pending["options"]["items"] == []
    complete, events = say(client, "5 лет", sid)
    result = diff(complete, events)
    assert result["before"]["travelers"]["children_ages"] == []
    assert result["after"]["travelers"]["children_ages"] == [5]
    reviewed, events = say(client, "Почему отель невозвратный?", sid)
    assert reviewed == complete and not any(row["type"] == "diff" for row in events)
    assert client.delete(f"/api/session/{sid}").status_code == 204
    fresh, _ = say(client, MAIN, sid)
    assert fresh["last_diff"] is None


def test_train_and_cross_transport_comparisons_keep_distinct_price_scopes(client):
    state, _ = say(client, MAIN)
    sid = state["session_id"]
    rail, events = say(client, "Теперь хочу в Петербург поездом", sid)
    result = diff(rail, events)
    assert (result["before"]["kind"], result["after"]["kind"]) == ("package", "train")
    assert {"transport", "route", "components"} <= set(result["comparison"]["differences"])
    assert not result["comparison"]["same_basis"]
    next_day, events = say(client, "На два дня позже", sid)
    result = diff(next_day, events)
    assert result["before"]["kind"] == result["after"]["kind"] == "train"
    assert result["after"]["known_total_rub"] == next_day["options"]["items"][0]["price_rub"]
    assert result["after"]["components"][0]["source"]["schedule_source"] == "synthetic"


def test_missing_transfer_and_unknown_cost_remain_null_even_when_known_components_exist(client):
    state, _ = say(client, MAIN)
    option = state["options"]["items"][0] | {"transfer": None, "total_rub": None}
    option["outbound"] = option["outbound"] | {"price_rub": None}
    brief = TripBrief.model_validate(state["brief"])
    summary = option_summary(option, brief)
    assert summary["total_rub"] is None
    costs = {row["key"]: row["known_cost_rub"] for row in summary["components"]}
    assert costs["arrival_transfer"] is costs["outbound"] is None
    changed = build_diff(
        before_brief=brief.model_dump(mode="json"),
        after_brief=brief,
        before=None,
        after=summary,
        sources={},
        version=1,
        status="updated",
        notes=[],
    )
    assert changed["comparison"]["delta_known_total_rub"] is None
    json.dumps(changed)


@pytest.mark.parametrize("minutes", [1, 59, 60, 61, 119, 180, 1439, 1440])
@pytest.mark.parametrize("water", [10, 24, 25.5, 35])
def test_fixed_favorite_numeric_clauses_round_trip_without_becoming_dates(minutes, water):
    duration = (
        f"Перелёт не больше {minutes // 60} часов."
        if minutes % 60 == 0
        else f"Максимальная длительность перелёта {minutes} минут."
    )
    patch = extract(duration + f" Температура воды не ниже {water} градусов.", date(2026, 10, 29))
    result = apply_patch(TripBrief(), {}, patch, today=date(2026, 10, 29))
    assert not result.rejected
    assert result.brief.max_flight_minutes == minutes and result.brief.min_water_temperature_c == water
    assert result.brief.start_date is None


def test_fixed_favorite_message_round_trips_all_filters_and_does_not_book(client):
    message = (
        "Новый поиск: из Шереметьево в Анталью, дата вылета 03.02.2027, 10 ночей, 1 взрослый. "
        "Дети: 0 лет, 5 лет, 5 лет. Питомцев: 2. Бюджет 200000 рублей на всю поездку за всех. "
        "Максимальная длительность перелёта 241 минут. Только прямые рейсы. Нужен вечерний вылет. "
        "Только возвратные тарифы отеля. Отель не менее 4 звёзд. Температура воды не ниже 25.5 градусов. "
        "Только направления без визы. Нужен семейный отель. Нужны отель и трансфер с питомцами. "
        "Нужны море и пляж. Нужен тёплый климат. Предпочитаю недорогие варианты. "
        "Предпочитаю короткий перелёт. Нужна романтическая поездка. Нужна круглосуточная стойка регистрации. "
        "Предпочитаю короткий трансфер. Нужен спокойный отдых. Планы могут поменяться, нужен возвратный тариф. "
        "Заново подобрать и пересчитать варианты. Ничего не выбирать, не оформлять и не оплачивать."
    )
    state, events = say(client, message)
    expected = {
        "origin": "SVO",
        "destination": "AYT",
        "start_date": "2027-02-03",
        "nights": 10,
        "adults": 1,
        "children_ages": [0, 5, 5],
        "pets": 2,
        "budget_rub": 200_000,
        "max_flight_minutes": 241,
        "direct_only": True,
        "late_departure": True,
        "refundable_only": True,
        "min_stars": 4,
        "min_water_temperature_c": 25.5,
        "visa_free_only": True,
    }
    assert {key: state["brief"][key] for key in expected} == expected
    assert set(state["brief"]["needs"]) == set(NEEDS)
    assert state["selected"] is state["booking"] is None and state["last_diff"] is None
    assert not any(row["type"] == "booking" for row in events)


@pytest.mark.parametrize(
    "question",
    [
        "Максимальная длительность перелёта 241 минут?",
        "Температура воды не ниже 25.5 градусов?",
        "Предпочитаю короткий перелёт?",
        "Нужна круглосуточная стойка регистрации?",
        "Предпочитаю короткий трансфер?",
    ],
)
def test_questions_do_not_apply_fixed_favorite_restore_clauses(question):
    assert extract(question, date(2026, 10, 29)).is_empty()


def test_model_misclassification_cannot_change_other_criteria_or_purchase_from_a_quick_action(client):
    from test_agent_trip_changes import MisclassifiedAnalysis

    before, _ = say(client, MAIN)
    sid = before["session_id"]
    chosen = next(row for row in before["options"]["items"] if row["can_confirm"])
    say(client, f"Беру вариант {chosen['index']}", sid)
    agent = client.app.state.agent
    agent._llm, agent._llm_writes = MisclassifiedAnalysis("confirm", {"destination": "MLE", "adults": 9}), True
    after, events = say(client, QUICK[1][0], sid)
    assert after["brief"]["destination"] == "AYT" and after["brief"]["adults"] == 2
    assert after["brief"]["direct_only"] is True
    assert after["selected"] is after["booking"] is None and not any(row["type"] == "booking" for row in events)
    diff(after, events)


def test_failed_turn_rolls_back_the_comparison_with_the_rest_of_the_session(client, monkeypatch):
    state, _ = say(client, MAIN)
    sid = state["session_id"]
    state, _ = say(client, "Теперь 20 ноября 2026", sid)

    async def fail(*args, **kwargs):
        raise RuntimeError("forced summary failure")

    monkeypatch.setattr(client.app.state.agent, "_summarize", fail)
    response = client.post("/api/chat", json={"message": "Теперь 22 ноября 2026", "session_id": sid})
    events = parse_sse(response.text)
    assert events[-1]["type"] == "error"
    assert client.get(f"/api/session/{sid}").json() == state
