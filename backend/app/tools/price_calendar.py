"""D8: calendar and complete flight+hotel date comparisons, independent of SSE/R3.

The caller supplies a search/quote adapter. Missing data stays missing; a cheap
flight alone cannot trigger a trip-saving recommendation. No provider is called
implicitly and no shared TripState or event format is introduced here.
"""

import calendar
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from app.contracts.identifiers import canonical_hotel_id
from app.contracts.tools import DayPrice, Flight, FlightSearch, PriceCalendarQuery, Travelers
from app.tools.flights import FlightTool

Rubles = Annotated[int, Field(strict=True, ge=0)]


class CalendarModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CalendarDay(CalendarModel):
    date: date
    min_price_rub: Rubles | None
    is_synthetic: bool | None


class PriceCalendar(CalendarModel):
    origin: str
    destination: str
    month: str
    days: tuple[CalendarDay, ...]
    price_scope: str = "one_way_all_travelers_excluding_baggage"


async def price_calendar(
    query: PriceCalendarQuery,
    search: Callable[[FlightSearch], Awaitable[list[Flight]]],
) -> PriceCalendar:
    """Minimum available one-way group price per day; no offers => null, not zero."""
    year, month = map(int, query.month.split("-"))
    days = []
    for number in range(1, calendar.monthrange(year, month)[1] + 1):
        day = date(year, month, number)
        offers = await search(
            FlightSearch(
                origin=query.origin,
                destination=query.destination,
                departure_date=day,
                travelers=query.travelers,
            )
        )
        valid = [
            item
            for item in offers
            if item.origin.iata == ("SVO" if query.origin == "MOW" else query.origin)
            and item.destination.iata == ("SVO" if query.destination == "MOW" else query.destination)
            and item.departure_at.date() == day
        ]
        cheapest = min(valid, key=lambda item: (item.price_rub, item.id), default=None)
        days.append(
            CalendarDay(
                date=day,
                min_price_rub=cheapest.price_rub if cheapest else None,
                is_synthetic=cheapest.is_synthetic if cheapest else None,
            )
        )
    return PriceCalendar(origin=query.origin, destination=query.destination, month=query.month, days=tuple(days))


class TripQuote(CalendarModel):
    """Comparable round trip and same physical hotel for an unchanged party/stay.

    flight_total includes both directions and required baggage; hotel_total is
    for all nights/rooms/travelers. Other invariant trip costs can be supplied.
    Missing any mandatory component means the adapter must return None instead.
    """

    departure_date: date
    nights: Annotated[int, Field(strict=True, ge=1)]
    origin: str
    destination: str
    hotel_id: str
    travelers: Travelers
    flight_total_rub: Rubles
    hotel_total_rub: Rubles
    other_total_rub: Rubles = 0
    is_synthetic: bool

    @field_validator("hotel_id")
    @classmethod
    def stable_hotel_identity(cls, value: str) -> str:
        return canonical_hotel_id(value)

    @model_validator(mode="after")
    def return_date_exists(self) -> "TripQuote":
        try:
            self.departure_date + timedelta(days=self.nights)
        except OverflowError as exc:
            raise ValueError("Return date is outside supported calendar") from exc
        return self

    @property
    def total_rub(self) -> int:
        return self.flight_total_rub + self.hotel_total_rub + self.other_total_rub


class DateSaving(CalendarModel):
    original_departure: date
    suggested_departure: date
    shift_days: int
    original_total_rub: Rubles
    suggested_total_rub: Rubles
    saving_rub: Rubles
    saving_percent: Decimal
    flight_delta_rub: int
    hotel_delta_rub: int
    other_delta_rub: int
    hotel_id: str
    is_synthetic: bool
    fact_type: str = "date_shift_saving"


async def suggest_date_shifts(
    selected: TripQuote,
    quote_trip: Callable[[date], Awaitable[TripQuote | None]],
    *,
    window_days: int = 3,
    minimum_saving_fraction: Decimal = Decimal("0.05"),
) -> tuple[DateSaving, ...]:
    """Strictly more than threshold, same hotel/party/nights, complete totals.

    Facts are suitable for an R3 adapter after O1; this module does not publish
    an SSE event. Price-source errors propagate; unavailable trips return None.
    """
    if isinstance(window_days, bool) or not isinstance(window_days, int) or not 1 <= window_days <= 3:
        raise ValueError("window_days must be 1..3")
    threshold = Decimal(str(minimum_saving_fraction))
    if not threshold.is_finite() or not Decimal(0) <= threshold < Decimal(1):
        raise ValueError("minimum_saving_fraction must be in [0, 1)")
    if selected.total_rub == 0:
        return ()
    numerator, denominator = threshold.as_integer_ratio()
    results = []
    for offset in range(-window_days, window_days + 1):
        if not offset:
            continue
        try:
            candidate_date = selected.departure_date + timedelta(days=offset)
        except OverflowError:
            continue
        candidate = await quote_trip(candidate_date)
        if candidate is None:
            continue
        if (
            candidate.departure_date != candidate_date
            or candidate.nights != selected.nights
            or candidate.origin != selected.origin
            or candidate.destination != selected.destination
            or candidate.hotel_id != selected.hotel_id
            or candidate.travelers != selected.travelers
            or candidate.is_synthetic != selected.is_synthetic
        ):
            continue
        saving = selected.total_rub - candidate.total_rub
        # Compare integers, so a caller's low Decimal precision cannot hide a
        # saving one ruble above the threshold or round one up into a fact.
        if saving <= 0 or saving * denominator <= selected.total_rub * numerator:
            continue
        with localcontext() as context:
            context.prec = max(28, len(str(saving)) + len(str(selected.total_rub)) + 12)
            percentage = (Decimal(saving) * 100 / Decimal(selected.total_rub)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        results.append(
            DateSaving(
                original_departure=selected.departure_date,
                suggested_departure=candidate_date,
                shift_days=offset,
                original_total_rub=selected.total_rub,
                suggested_total_rub=candidate.total_rub,
                saving_rub=saving,
                saving_percent=percentage,
                flight_delta_rub=candidate.flight_total_rub - selected.flight_total_rub,
                hotel_delta_rub=candidate.hotel_total_rub - selected.hotel_total_rub,
                other_delta_rub=candidate.other_total_rub - selected.other_total_rub,
                hotel_id=selected.hotel_id,
                is_synthetic=candidate.is_synthetic,
            )
        )
    return tuple(sorted(results, key=lambda item: (-item.saving_rub, abs(item.shift_days), item.shift_days)))


class PriceCalendarTool:
    name = "price_calendar"
    description = (
        "Минимальная цена перелёта в одну сторону за всю группу по дням месяца, без багажа; неизвестные дни пропущены."
    )
    input_model = PriceCalendarQuery
    output_adapter = TypeAdapter(list[DayPrice])

    def __init__(self, *, flights: FlightTool | None = None):
        self.flights = flights if flights is not None else FlightTool()

    async def run(self, input: PriceCalendarQuery) -> list[DayPrice]:
        result = await price_calendar(input, self.flights.run)
        return [
            DayPrice(date=day.date, price_rub=day.min_price_rub, is_synthetic=day.is_synthetic)
            for day in result.days
            if day.min_price_rub is not None
        ]
