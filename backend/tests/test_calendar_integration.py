from datetime import date
from decimal import Decimal, localcontext

import pytest

from app.contracts.tools import FlightSearch, PriceCalendarQuery, Travelers
from app.tools.flights import FlightTool
from app.tools.price_calendar import PriceCalendarTool, TripQuote, price_calendar, suggest_date_shifts
from app.tools.registry import run_tool


@pytest.mark.parametrize("month", ["2026-11", "2028-02", "2026-12"])
async def test_calendar_matches_actual_d2_minimum_for_every_day_and_group(month):
    flights = FlightTool()
    query = PriceCalendarQuery(
        origin="MOW", destination="DXB", month=month, travelers=Travelers(adults=2, children_ages=[0, 5])
    )
    tool = PriceCalendarTool(flights=flights)
    result = await run_tool(tool.name, query.model_dump(), tools={tool.name: tool})
    assert len(result) in (29, 30, 31)
    for day in result:
        offers = await flights.run(
            FlightSearch(
                origin=query.origin, destination=query.destination, departure_date=day.date, travelers=query.travelers
            )
        )
        assert day.price_rub == min(item.price_rub for item in offers)
        assert day.is_synthetic


async def test_unknown_route_keeps_full_null_calendar_but_d1_has_no_invented_days():
    query = PriceCalendarQuery(origin="JFK", destination="DXB", month="2026-11")
    full = await price_calendar(query, FlightTool().run)
    assert len(full.days) == 30 and all(item.min_price_rub is None for item in full.days)
    assert await PriceCalendarTool().run(query) == []


async def test_wrong_dates_or_routes_never_enter_a_calendar_day():
    actual = FlightTool()

    async def wrong(query):
        return await actual.run(query.model_copy(update={"departure_date": date(2026, 12, 1)}))

    result = await price_calendar(PriceCalendarQuery(origin="SVO", destination="DXB", month="2026-11"), wrong)
    assert all(item.min_price_rub is None for item in result.days)


async def test_errors_propagate_instead_of_becoming_unknown_or_zero():
    async def broken(query):
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError):
        await price_calendar(PriceCalendarQuery(origin="SVO", destination="DXB", month="2026-11"), broken)


async def test_threshold_comparison_is_exact_even_with_low_decimal_precision():
    selected = TripQuote(
        departure_date=date(2026, 11, 18),
        nights=7,
        origin="SVO",
        destination="DXB",
        hotel_id="osm:node/11182077737",
        travelers=Travelers(adults=2),
        flight_total_rub=60_000,
        hotel_total_rub=40_000,
        is_synthetic=True,
    )

    async def candidate(day):
        return selected.model_copy(update={"departure_date": day, "hotel_total_rub": 34_999})

    with localcontext() as ctx:
        ctx.prec = 2
        results = await suggest_date_shifts(selected, candidate, window_days=1)
    assert len(results) == 2 and all(item.saving_rub == 5001 for item in results)
    assert all(item.saving_percent == Decimal("5.00") for item in results)


async def test_date_extremes_do_not_overflow_and_zero_total_has_no_saving():
    selected = TripQuote(
        departure_date=date.min,
        nights=1,
        origin="SVO",
        destination="DXB",
        hotel_id="osm:node/11182077737",
        travelers=Travelers(),
        flight_total_rub=100,
        hotel_total_rub=0,
        is_synthetic=True,
    )
    seen = []

    async def unavailable(day):
        seen.append(day)
        return None

    assert await suggest_date_shifts(selected, unavailable) == ()
    assert len(seen) == 3
    assert await suggest_date_shifts(selected.model_copy(update={"flight_total_rub": 0}), unavailable) == ()
