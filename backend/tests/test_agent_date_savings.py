"""D8 comparisons use full comparable components and never mutate the trip."""

import copy
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.agent.brief import TripBrief
from app.agent.date_savings import compare_date_savings, is_date_savings_question
from app.agent.planner import search_trip
from app.agent.session import Session, Stage
from app.config import Settings
from app.contracts.tools import FlightPriceSource, FlightSearch
from app.tools.flights import FlightTool
from app.tools.registry import create_tools, run_tool

DAY = date(2026, 11, 18)
TODAY = date(2026, 10, 28)


@pytest.fixture
async def setup_trip():
    tools = create_tools(
        Settings(_env_file=None, tools_mode="real", flights_mode="synthetic", cache_mode="replay", llm_provider="fake")
    )

    async def call(name, arguments):
        rows = await run_tool(name, arguments, tools=tools)
        if name == "search_flights" and rows:
            return [min(rows, key=lambda row: row.departure_at)]
        return rows

    brief = TripBrief(origin="MOW", destination="AYT", start_date=DAY, nights=7, adults=2)
    plan = await search_trip(brief, call, TODAY)
    assert plan.options
    session = Session(brief=brief, packages=plan.options, stage=Stage.PACKAGES_SHOWN)
    baseline = session.packages[0]
    for direction in ("outbound", "inbound"):
        baseline[direction]["price_rub"] = 30_000
        baseline[direction]["baggage_price_rub"] = 5_000
    baseline["hotel"]["price_rub"] = 29_000
    baseline["transfer"]["price_rub"] = 1_000
    baseline["total_rub"] = baseline["known_total_rub"] = 100_000
    try:
        yield session, call
    finally:
        await tools.aclose()


def priced_call(
    call,
    *,
    hotel_price=23_999,
    seat_price=30_000,
    baggage_price=5_000,
    transfer_price=1_000,
    missing=None,
    provider=False,
):
    calls = []

    async def priced(name, arguments):
        calls.append((name, copy.deepcopy(arguments)))
        if missing == "provider_error":
            raise RuntimeError("PRIVATE-CANARY-DO-NOT-EXPOSE")
        if name == "search_flights" and arguments["origin"] == "AYT" and missing == "inbound":
            return []
        if name == "search_hotels" and missing == "hotel":
            return []
        if name == "get_transfer" and missing == "transfer":
            return None
        rows = await call(name, arguments)
        if name == "search_flights":
            source = (
                FlightPriceSource(
                    seat_price_rub=seat_price // 2,
                    cache_mode="replay",
                    cache_hit=True,
                    duration_is_modelled=True,
                )
                if provider
                else None
            )
            return [
                row.model_copy(
                    update={"price_rub": seat_price, "baggage_price_rub": baggage_price, "price_source": source}
                )
                for row in rows
            ]
        if name == "search_hotels":
            return [row.model_copy(update={"price_rub": hotel_price}) for row in rows]
        if name == "get_transfer":
            return [row.model_copy(update={"price_rub": transfer_price}) for row in rows]
        return rows

    return priced, calls


@pytest.mark.parametrize(
    "text",
    [
        "Если сдвинуть даты на 1–3 дня, будет дешевле?",
        "А если на два дня позже?",
        "Что если на день раньше, сколько будет стоить?",
        "Сравни даты раньше и позже",
        "Можно сэкономить на других датах?",
    ],
)
def test_comparison_questions(text):
    assert is_date_savings_question(text)


@pytest.mark.parametrize(
    "text",
    [
        "Перенеси поездку на два дня позже",
        "Сдвинь вылет на один день раньше",
        "На два дня позже",
        "Хочу на три дня позже",
        "Дешевле другой отель?",
        "Что раньше: аэропорт или отель?",
    ],
)
def test_actual_reschedule_and_unrelated_questions_are_not_comparisons(text):
    assert not is_date_savings_question(text)


async def test_same_hotel_full_stay_roundtrip_baggage_transfer_and_strict_threshold(setup_trip):
    session, call = setup_trip
    before = copy.deepcopy(session.__dict__)
    priced, calls = priced_call(call)
    result = await compare_date_savings(session, priced, today=TODAY)
    facts = result.facts
    assert facts["comparison_status"] == "compared"
    assert facts["baseline_assumption"] == "За основу взят вариант 1 из показанных."
    assert facts["baseline_known_total_rub"] == 100_000
    assert len(facts["checked_dates"]) == len(facts["suggestions"]) == 6
    assert facts["price_scope"] == "round_trip_flights_baggage_hotel_arrival_transfer"
    for row in facts["checked_dates"]:
        quote = row["quote"]
        assert quote["flight_total_rub"] == 70_000  # Includes both baggage charges.
        assert quote["hotel_total_rub"] == 23_999
        assert quote["other_total_rub"] == 1_000
        assert quote["hotel_id"] == facts["baseline"]["hotel_id"]
        assert quote["nights"] == 7 and quote["travelers"] == {"adults": 2, "children_ages": [], "pets": 0}
    for saving in facts["suggestions"]:
        assert saving["saving_rub"] == 5_001
        assert Decimal(saving["saving_percent"]) == Decimal("5.00")  # Display rounded; comparison is exact.
        assert saving["saving_rub"] * 100 > saving["original_total_rub"] * 5
        assert saving["original_total_rub"] - saving["suggested_total_rub"] == saving["saving_rub"]
        assert saving["hotel_delta_rub"] == -5_001
    assert len([args for name, args in calls if name == "search_flights"]) == 12
    stays = [args for name, args in calls if name == "search_hotels"]
    assert len(stays) == 6
    assert all((date.fromisoformat(row["check_out"]) - date.fromisoformat(row["check_in"])).days == 7 for row in stays)
    assert all(args["travelers"] == session.brief.travelers.model_dump() for _, args in calls)
    assert "полная стоимость поездки и экономия по ней не подтверждены" in result.text
    assert "обратный трансфер, виза" in result.text
    assert facts["state_changed"] is False and session.__dict__ == before
    json.dumps(facts, ensure_ascii=False)


async def test_exact_five_percent_is_not_recommended(setup_trip):
    session, call = setup_trip
    priced, _ = priced_call(call, hotel_price=24_000)
    result = await compare_date_savings(session, priced, today=TODAY)
    assert result.facts["comparison_status"] == "compared"
    assert result.facts["suggestions"] == []
    assert "нет варианта с экономией строго больше 5%" in result.text


async def test_cheaper_seats_with_more_expensive_baggage_do_not_create_false_savings(setup_trip):
    session, call = setup_trip
    priced, _ = priced_call(call, seat_price=1_000, baggage_price=50_000)
    result = await compare_date_savings(session, priced, today=TODAY)
    assert result.facts["suggestions"] == []
    assert all(row["quote"]["flight_total_rub"] == 102_000 for row in result.facts["checked_dates"])


@pytest.mark.parametrize("missing", ["inbound", "hotel", "transfer", "provider_error"])
async def test_missing_required_quote_never_produces_a_saving_or_leaks_exception(setup_trip, missing):
    session, call = setup_trip
    priced, _ = priced_call(call, missing=missing)
    result = await compare_date_savings(session, priced, today=TODAY)
    assert result.facts["comparison_status"] == "unavailable"
    assert result.facts["suggestions"] == []
    assert "Экономию подтвердить нельзя" in result.text
    assert "PRIVATE-CANARY" not in json.dumps(result.facts) + result.text
    if missing == "provider_error":
        assert {row["error_type"] for row in result.facts["checked_dates"]} == {"RuntimeError"}


async def test_same_physical_hotel_required_even_if_other_hotels_are_cheaper(setup_trip):
    session, call = setup_trip
    target = session.packages[0]["hotel"]["hotel_id"]
    priced, _ = priced_call(call)

    async def different_hotel(name, arguments):
        rows = await priced(name, arguments)
        return [row for row in rows if row.hotel_id != target] if name == "search_hotels" else rows

    result = await compare_date_savings(session, different_hotel, today=TODAY)
    assert result.facts["suggestions"] == []


async def test_changed_refund_conditions_are_not_used_to_manufacture_savings(setup_trip):
    session, call = setup_trip
    original = session.packages[0]["hotel"]["refundable"]
    priced, _ = priced_call(call)

    async def different_terms(name, arguments):
        rows = await priced(name, arguments)
        if name == "search_hotels":
            return [row.model_copy(update={"refundable": not original}) for row in rows]
        return rows

    result = await compare_date_savings(session, different_terms, today=TODAY)
    assert result.facts["suggestions"] == []


async def test_explicit_second_variant_compares_its_hotel_without_selecting_it(setup_trip):
    session, call = setup_trip
    assert len(session.packages) >= 2
    before = copy.deepcopy(session.__dict__)
    priced, _ = priced_call(call, seat_price=100, baggage_price=100, hotel_price=100, transfer_price=100)
    result = await compare_date_savings(session, priced, today=TODAY, text="Для второго варианта другие даты дешевле?")
    assert result.facts["baseline_option"] == 2
    assert result.facts["baseline_assumption"] is None
    assert result.facts["hotel_id"] == session.packages[1]["hotel"]["hotel_id"]
    assert result.facts["suggestions"]
    assert before == session.__dict__


async def test_provider_baseline_never_accepts_synthetic_flights(setup_trip):
    session, call = setup_trip
    source = FlightPriceSource(
        seat_price_rub=15_000, cache_mode="replay", cache_hit=True, duration_is_modelled=True
    ).model_dump(mode="json")
    for leg in ("outbound", "inbound"):
        session.packages[0][leg]["price_source"] = source
    priced, _ = priced_call(call)
    absent = await compare_date_savings(session, priced, today=TODAY)
    assert absent.facts["flight_source"] == "aviasales_data_api"
    assert absent.facts["suggestions"] == [] and absent.facts["comparison_status"] == "unavailable"
    provider, _ = priced_call(call, provider=True)
    present = await compare_date_savings(session, provider, today=TODAY)
    assert present.facts["suggestions"] and "поискового кэша Aviasales" in present.text


async def test_explicit_single_day_and_selected_option_have_no_implicit_state_change(setup_trip):
    session, call = setup_trip
    session.selected = 1
    session.stage = Stage.AWAITING_CONFIRMATION
    before = copy.deepcopy(session.__dict__)
    priced, calls = priced_call(call)
    result = await compare_date_savings(session, priced, today=TODAY, text="А если на два дня позже?")
    assert result.facts["baseline_assumption"] is None
    assert len(result.facts["checked_dates"]) == 1
    assert result.facts["checked_dates"][0]["departure_date"] == "2026-11-20"
    assert {args["departure_date"] for name, args in calls if name == "search_flights"} == {
        "2026-11-20",
        "2026-11-27",
    }
    assert before == session.__dict__


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Что если на день раньше?", {-1}),
        ("А если раньше на 2 дня?", {-2}),
        ("Можно дешевле на два дня раньше или позже?", {-2, 2}),
        ("Если сдвинуть на 1–3 дня, дешевле?", {-3, -2, -1, 1, 2, 3}),
    ],
)
async def test_requested_direction_and_distance_are_respected(setup_trip, text, expected):
    session, call = setup_trip
    priced, _ = priced_call(call)
    result = await compare_date_savings(session, priced, today=TODAY, text=text)
    assert {row["shift_days"] for row in result.facts["checked_dates"]} == expected


async def test_outside_supported_window_is_not_silently_changed(setup_trip):
    session, _ = setup_trip

    async def forbidden(*_):
        pytest.fail("Do not silently search a different date shift")

    result = await compare_date_savings(session, forbidden, today=TODAY, text="А если на 4 дня позже?")
    assert result.facts["reason"] == "unsupported_shift"


async def test_unpriced_arrival_transfer_is_excluded_from_both_sides_and_named(setup_trip):
    session, call = setup_trip
    package = session.packages[0]
    package["transfer"] = None
    package["known_total_rub"] -= 1_000
    package["total_rub"] = None
    priced, calls = priced_call(call)
    result = await compare_date_savings(session, priced, today=TODAY)
    assert result.facts["comparison_status"] == "compared"
    assert result.facts["price_scope"] == "round_trip_flights_baggage_hotel"
    assert "arrival_transfer" in result.facts["unpriced_components"]
    assert not any(name == "get_transfer" for name, _ in calls)
    assert all(row["quote"]["other_total_rub"] == 0 for row in result.facts["checked_dates"])
    assert "Не оценены оба трансфера" in result.text


async def test_stale_unknown_or_incomplete_baseline_makes_no_search(setup_trip):
    session, _ = setup_trip

    async def forbidden(*_):
        pytest.fail("No search should start from an invalid baseline")

    session.stale = True
    assert (await compare_date_savings(session, forbidden, today=TODAY)).facts["reason"] == "stale_options"
    session.stale = False
    assert (
        await compare_date_savings(session, forbidden, today=TODAY, text="Вариант 99 дешевле на других датах?")
    ).facts["reason"] == "unknown_option"
    session.packages[0]["known_total_rub"] += 1
    assert (await compare_date_savings(session, forbidden, today=TODAY)).facts["reason"] == "incomplete_baseline"


async def test_past_date_is_not_searched_and_real_registry_comparison_is_reproducible(setup_trip):
    session, call = setup_trip
    before = copy.deepcopy(session.__dict__)
    first = await compare_date_savings(session, call, today=DAY - timedelta(days=1))
    second = await compare_date_savings(session, call, today=DAY - timedelta(days=1))
    assert first == second and before == session.__dict__
    statuses = {row["shift_days"]: row["status"] for row in first.facts["checked_dates"]}
    assert all(statuses[shift] == "past_date" for shift in (-3, -2, -1))
    assert all(statuses[shift] == "quoted" for shift in (1, 2, 3))


async def test_overnight_outbound_keeps_seven_hotel_nights_and_returns_from_actual_arrival_date(setup_trip):
    session, call = setup_trip
    priced, calls = priced_call(call)

    async def overnight(name, arguments):
        if name == "search_flights" and arguments["origin"] == "MOW":
            calls.append((name, copy.deepcopy(arguments)))
            flights = await FlightTool().run(FlightSearch.model_validate(arguments))
            return [max(flights, key=lambda row: row.departure_at)]
        rows = await priced(name, arguments)
        if name == "search_hotels":
            return [row.model_copy(update={"reception_24h": True, "reception_closes_at": None}) for row in rows]
        return rows

    result = await compare_date_savings(session, overnight, today=TODAY, text="А если на два дня позже?")
    assert result.facts["comparison_status"] == "compared"
    stays = [args for name, args in calls if name == "search_hotels"]
    assert stays[0]["check_in"] == "2026-11-21"
    assert stays[0]["check_out"] == "2026-11-28"
    inbound = [args for name, args in calls if name == "search_flights" and args["origin"] == "AYT"]
    assert inbound[0]["departure_date"] == "2026-11-28"
    assert result.facts["checked_dates"][0]["quote"]["nights"] == 7


async def test_late_departure_never_uses_cheaper_daytime_outbound_but_allows_daytime_return(setup_trip):
    session, call = setup_trip
    session.brief.late_departure = True
    priced, _ = priced_call(call, hotel_price=29_000)

    async def full_timetable(name, arguments):
        if name != "search_flights":
            return await priced(name, arguments)
        rows = await FlightTool().run(FlightSearch.model_validate(arguments))
        return [
            row.model_copy(
                update={
                    # A very cheap outbound at 09:00 violates the selected constraint.
                    # On the return leg the same 09:00 departure remains valid.
                    "price_rub": (
                        100_000 if row.departure_at.hour >= 18 else 1 if arguments["origin"] == "MOW" else 30_000
                    ),
                    "baggage_price_rub": 5_000,
                }
            )
            for row in rows
        ]

    plan = await search_trip(session.brief, full_timetable, TODAY)
    assert plan.options
    session.packages = plan.options
    assert session.packages[0]["outbound"]["departure_at"].startswith("2026-11-18T23:30:")
    assert session.packages[0]["inbound"]["departure_at"].startswith("2026-11-26T09:00:")
    result = await compare_date_savings(session, full_timetable, today=TODAY, text="А если на два дня позже?")
    assert result.facts["comparison_status"] == "compared"
    assert result.facts["baseline"]["flight_total_rub"] == 140_000
    assert result.facts["checked_dates"][0]["quote"]["flight_total_rub"] == 140_000
    assert result.facts["suggestions"] == []
