from datetime import date
from decimal import Decimal

import pytest

from app.contracts.tools import PriceCalendarQuery, Travelers
from app.tools.price_calendar import TripQuote, price_calendar, suggest_date_shifts
from app.tools.stubs import create_stub_tools


@pytest.fixture
def selected():
    return TripQuote(
        departure_date=date(2026, 11, 15),
        nights=7,
        origin="SVO",
        destination="DXB",
        hotel_id="osm-way-123",
        travelers=Travelers(adults=2),
        flight_total_rub=60000,
        hotel_total_rub=40000,
        is_synthetic=True,
    )


async def test_savings_use_complete_total_and_strict_threshold(selected):
    async def quote(day):
        shift = (day - selected.departure_date).days
        # Cheaper flights can be cancelled out by more expensive hotel nights.
        prices = {-3: (40000, 65000), -2: (55000, 40000), -1: (50000, 40000), 1: (55000, 30000)}
        if shift not in prices:
            return None
        flight, hotel = prices[shift]
        return selected.model_copy(update={"departure_date": day, "flight_total_rub": flight, "hotel_total_rub": hotel})

    facts = await suggest_date_shifts(selected, quote)
    assert [(fact.shift_days, fact.saving_rub) for fact in facts] == [(1, 15000), (-1, 10000)]
    assert facts[0].saving_percent == Decimal("15.00")
    assert facts[0].flight_delta_rub + facts[0].hotel_delta_rub + facts[0].other_delta_rub == -15000


@pytest.mark.parametrize(
    "changed",
    [
        {"hotel_id": "other"},
        {"nights": 8},
        {"travelers": Travelers(adults=1)},
        {"destination": "AYT"},
        {"is_synthetic": False},
    ],
)
async def test_incompatible_quote_cannot_claim_savings(selected, changed):
    async def quote(day):
        return selected.model_copy(update={"departure_date": day, "flight_total_rub": 0, **changed})

    assert await suggest_date_shifts(selected, quote) == ()


async def test_calendar_covers_leap_month_and_missing_days():
    query = PriceCalendarQuery(origin="SVO", destination="DXB", month="2028-02")
    tool = create_stub_tools()["search_flights"]

    async def search(request):
        return [] if request.departure_date.day == 3 else await tool.run(request)

    result = await price_calendar(query, search)
    assert len(result.days) == 29
    assert result.days[2].min_price_rub is None
    assert result.days[0].min_price_rub > 0
    assert result.days[-1].date == date(2028, 2, 29)


async def test_invalid_threshold(selected):
    async def quote(_):
        pytest.fail("No provider calls for invalid input")

    with pytest.raises(ValueError):
        await suggest_date_shifts(selected, quote, minimum_saving_fraction=Decimal("NaN"))
