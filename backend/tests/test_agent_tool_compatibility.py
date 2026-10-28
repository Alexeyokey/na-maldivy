"""Regression checks work with both original D1 and the nullable D5 projection."""

from datetime import date
from types import SimpleNamespace

import pytest

from app.agent import formatting as fmt
from app.agent import planner
from app.agent.brief import TripBrief, apply_patch
from app.agent.core import TravelAgent
from app.agent.hidden import extract
from app.agent.planner import search_trip, suggest
from app.agent.session import Session, Stage
from app.llm import FakeLLMClient
from app.tools.registry import run_tool
from app.tools.stubs import create_stub_tools

TODAY = date(2026, 10, 28)


async def destinations_with_nulls():
    tool = create_stub_tools()["suggest_destinations"]
    rows = await tool.run(tool.input_model(month=11, origin="SVO"))
    return [
        SimpleNamespace(
            **(
                dict(row.__dict__)
                | {
                    "air_temperature_c": None,
                    "water_temperature_c": None,
                    "flight_duration_minutes": None,
                    "estimated_total_price_rub": None,
                    "visa_required": None,
                }
            )
        )
        for row in rows
    ]


async def test_unknown_destinations_do_not_crash_or_claim_visa_free_or_over_budget():
    rows = await destinations_with_nulls()
    calls = []

    async def call(name, arguments):
        calls.append(arguments)
        return [] if arguments["budget_rub"] is not None else rows

    result = await suggest(TripBrief(origin="SVO", month=11, budget_rub=300000), call)
    assert result.options and len(calls) == 2
    assert all(row["estimated_total_rub"] is None and row["visa_required"] is None for row in result.options)
    text = fmt.destinations(result)
    assert "цена неизвестна" in text
    assert "Нет данных" in text or "нет данных" in text
    assert "виза не нужна" not in text and "дороже бюджета" not in text
    assert "бюджет пока не проверен" in text


@pytest.mark.parametrize(
    "field,value", [("visa_free_only", True), ("min_water_temperature_c", 24), ("max_flight_minutes", 300)]
)
async def test_unknown_values_do_not_satisfy_explicit_hard_filters(field, value):
    rows = await destinations_with_nulls()

    async def call(name, arguments):
        return rows

    result = await suggest(TripBrief(origin="SVO", month=11, **{field: value}), call)
    assert result.options == []


async def test_source_failure_is_not_reported_as_a_budget_miss():
    async def call(name, arguments):
        return None

    result = await suggest(TripBrief(origin="SVO", month=11, budget_rub=300000), call)
    assert not result.options
    assert "недоступен" in fmt.destinations(result)
    assert "превышают бюджет" not in fmt.destinations(result)


async def test_mixed_known_unknown_and_zero_values_sort_and_serialize():
    rows = await destinations_with_nulls()
    rows[0].estimated_total_price_rub = 0
    rows[0].flight_duration_minutes = 0 + 60
    rows[0].water_temperature_c = 24
    rows[0].air_temperature_c = -1

    async def call(name, arguments):
        return rows

    result = await suggest(TripBrief(origin="SVO", month=11, needs=["short_flight"]), call)
    assert result.options[0]["estimated_total_rub"] == 0
    text = fmt.destinations(result)
    assert "0 ₽" in text and "воздух -1" in text and "+-1" not in text


async def stub_plan(*, missing_transfer=False, broken_validation=False, monkeypatch=None):
    tools = create_stub_tools()

    async def call(name, arguments):
        if name == "get_transfer" and missing_transfer:
            return None
        return await run_tool(name, arguments, tools=tools)

    if broken_validation:

        def broken(*args, **kwargs):
            raise RuntimeError("validator unavailable")

        monkeypatch.setattr(planner, "validate_trip", broken)
    return await search_trip(
        TripBrief(origin="SVO", destination="DXB", start_date=date(2026, 11, 18), nights=7), call, TODAY
    )


async def test_baggage_is_included_exactly_once_in_scoped_price():
    result = await stub_plan()
    assert result.options
    for row in result.options:
        expected = sum(row[leg]["price_rub"] + row[leg]["baggage_price_rub"] for leg in ("outbound", "inbound"))
        expected += row["hotel"]["price_rub"] + row["transfer"]["price_rub"]
        assert row["total_rub"] == expected
        assert row["price_scope"] == "round_trip_flights_baggage_hotel_arrival_transfer"
    text = fmt.packages(result)
    assert "виза и прочие" in text
    assert "багаж отдельно" not in text


async def test_missing_transfer_keeps_unknown_total_and_blocks_confirmation():
    result = await stub_plan(missing_transfer=True)
    assert result.options
    assert all(
        row["total_rub"] is None and row["known_total_rub"] > 0 and not row["can_confirm"] for row in result.options
    )
    assert "цена неполная" in fmt.packages(result)
    assert "дешевле всех" not in fmt.packages(result)
    agent = TravelAgent(FakeLLMClient(), create_stub_tools(), llm_writes_text=False)
    session = Session(packages=result.options, stage=Stage.PACKAGES_SHOWN)
    from app.agent.router import Action, Decision

    text = agent._request_confirmation(session, Decision(Action.CONFIRM, "test", option=1), lambda *args: None)
    assert "Подтверждение недоступно" in text
    assert session.stage == Stage.PACKAGES_SHOWN


async def test_validator_failure_is_explicit_and_never_an_all_clear(monkeypatch):
    result = await stub_plan(broken_validation=True, monkeypatch=monkeypatch)
    assert result.options
    assert all(row["validation_status"] == "failed" and not row["can_confirm"] for row in result.options)
    assert "Проверка стыковок не завершена" in fmt.packages(result)


async def test_confirmation_does_not_turn_unknown_refund_into_nonrefundable():
    result = await stub_plan()
    row = result.options[0]
    row["hotel"]["refundable"] = None
    text = fmt.confirmation(row, TripBrief())
    assert "условия возврата неизвестны" in text
    assert "без возврата" not in text
    assert "бесплатная отмена" not in text


async def turn(agent, text, sid=None):
    events = [event async for event in agent.run(text, sid)]
    assert events[-1].type == "done"
    return events


async def test_rail_query_uses_d6_and_preserves_group_and_class_without_booking():
    agent = TravelAgent(FakeLLMClient(), create_stub_tools(), llm_writes_text=False, today=lambda: TODAY)
    events = await turn(agent, "Питер Москва поездом 18 ноября 2026 купе на двоих")
    calls = [e.data for e in events if e.type == "tool_call"]
    assert len(calls) == 1 and calls[0]["name"] == "search_trains"
    assert calls[0]["arguments"] == {
        "origin": "LED",
        "destination": "MOW",
        "departure_date": "2026-11-18",
        "seat_class": "coupe",
        "travelers": {"adults": 2, "children_ages": [], "pets": 0},
    }
    assert events[-1].data["stage"] == Stage.TRAINS_SHOWN
    sid = events[-1].data["session_id"]
    events = await turn(agent, "а плацкарт?", sid)
    assert [e.data for e in events if e.type == "tool_call"][0]["arguments"]["seat_class"] == "platzkart"
    events = await turn(agent, "беру первый, оформляй", sid)
    assert not any(e.type == "booking" for e in events)
    assert events[-1].data["stage"] == Stage.TRAINS_SHOWN


async def test_rail_without_exact_date_asks_and_does_not_call_flight_calendar():
    agent = TravelAgent(FakeLLMClient(), create_stub_tools(), llm_writes_text=False, today=lambda: TODAY)
    events = await turn(agent, "из Москвы в Питер поездом в ноябре")
    assert [e.data for e in events if e.type == "question"][0]["field"] == "start_date"
    assert not any(e.type == "tool_call" for e in events)


@pytest.mark.parametrize("text", ["18 ноября 2025", "2025-11-18"])
def test_explicit_past_year_is_rejected_instead_of_rolled_forward(text):
    result = apply_patch(TripBrief(), {}, extract(text, TODAY), today=TODAY)
    assert result.brief.start_date is None
    assert any(r.field == "start_date" for r in result.rejected)


def test_d5_iata_and_rail_cities_are_not_limited_to_three_seed_destinations():
    assert TripBrief(destination="HKT").destination == "HKT"
    assert TripBrief(transport="train", destination="Москва").destination == "MOW"


def test_free_dates_do_not_match_sv_train_class():
    assert extract("хочу на море, свободные даты в ноябре", TODAY).transport is None


async def test_flight_limit_is_checked_against_actual_elapsed_duration():
    async def call(name, arguments):
        return await run_tool(name, arguments, tools=create_stub_tools())

    result = await search_trip(
        TripBrief(origin="SVO", destination="MLE", start_date=date(2026, 11, 18), max_flight_minutes=60),
        call,
        TODAY,
    )
    assert not result.options


async def test_hotel_source_failure_is_distinguished_from_no_offers():
    async def call(name, arguments):
        if name == "search_hotels":
            return None
        return await run_tool(name, arguments, tools=create_stub_tools())

    result = await search_trip(TripBrief(origin="SVO", destination="DXB", start_date=date(2026, 11, 18)), call, TODAY)
    assert not result.options and "недоступен" in fmt.packages(result)
    assert "нет отелей" not in fmt.packages(result)


@pytest.mark.parametrize("field,value", [("refundable_only", True), ("min_stars", 4)])
def test_hotel_unknown_does_not_satisfy_hard_tariff_or_star_filter(field, value):
    hotel = SimpleNamespace(pets_allowed=None, adults_only=None, refundable=None, stars=None)
    assert not planner._hotel_fits(hotel, TripBrief(**{field: value}))


async def test_calendar_failure_is_not_empty_inventory():
    async def call(name, arguments):
        return None

    result = await search_trip(TripBrief(origin="SVO", destination="DXB", month=11), call, TODAY)
    assert not result.options and "Календарь цен недоступен" in fmt.packages(result)
    assert "нет рейсов" not in fmt.packages(result)


async def test_known_costs_over_budget_are_not_offered_as_affordable():
    async def call(name, arguments):
        return await run_tool(name, arguments, tools=create_stub_tools())

    result = await search_trip(
        TripBrief(origin="SVO", destination="DXB", start_date=date(2026, 11, 18), budget_rub=1), call, TODAY
    )
    assert not result.options and "превышают бюджет" in fmt.packages(result)


async def test_tool_source_error_does_not_disclose_paths_to_chat():
    tools = create_stub_tools()

    class Broken:
        input_model = tools["search_flights"].input_model

        async def run(self, query):
            raise FileNotFoundError("/private/secret/user-data.json")

    tools["search_flights"] = Broken()
    agent = TravelAgent(FakeLLMClient(), tools, llm_writes_text=False, today=lambda: TODAY)
    events = await turn(agent, "из Москвы в Дубай 18 ноября 2026 на неделю")
    assert any(e.type == "tool_result" and e.data["ok"] is False for e in events)
    assert "/private/secret" not in str([e.model_dump() for e in events])
