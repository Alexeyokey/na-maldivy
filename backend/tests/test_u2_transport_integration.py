"""D2/D8 integration: dates, complete group totals, aliases and offline demo."""

import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app import u2_cli
from app.contracts.tools import Flight, FlightSearch, HotelSearch, Place, PriceCalendarQuery, Travelers
from app.tools.flights import FlightTool, generate_flights
from app.tools.hotels import search_hotel_results
from app.tools.price_calendar import TripQuote, price_calendar, suggest_date_shifts
from app.tools.transport import arrival_after


@pytest.mark.parametrize("origin", ["MOW", "SVO", "LED"])
@pytest.mark.parametrize("destination", ["AYT", "DXB", "MLE"])
@pytest.mark.parametrize("seed", [42, 777])
async def test_entire_leap_month_calendar_equals_d2_group_minimum(origin, destination, seed):
    party = Travelers(adults=2, children_ages=[3, 12], pets=1)
    query = PriceCalendarQuery(origin=origin, destination=destination, month="2028-02", travelers=party)
    calendar = await price_calendar(query, FlightTool(mode="synthetic", seed=seed).run)
    assert len(calendar.days) == 29
    assert calendar.price_scope == "one_way_all_travelers_excluding_baggage"
    for cell in calendar.days:
        offers = generate_flights(
            FlightSearch(
                origin=origin,
                destination=destination,
                departure_date=cell.date,
                travelers=party,
            ),
            seed=seed,
        )
        assert cell.min_price_rub == min(offer.price_rub for offer in offers)
        assert cell.is_synthetic is True
        # The scalar already covers four people and one pet; baggage is separate.
        solo = generate_flights(
            FlightSearch(
                origin=origin,
                destination=destination,
                departure_date=cell.date,
            ),
            seed=seed,
        )
        assert cell.min_price_rub == min(offer.price_rub for offer in solo) * 4 + 1500


async def test_calendar_reverse_moscow_alias_and_local_departure_date():
    query = PriceCalendarQuery(origin="DXB", destination="MOW", month="2030-12")
    calendar = await price_calendar(query, FlightTool(seed=18).run)
    assert len(calendar.days) == 31
    last = generate_flights(
        FlightSearch(
            origin="DXB",
            destination="MOW",
            departure_date=date(2030, 12, 31),
        ),
        seed=18,
    )
    assert calendar.days[-1].min_price_rub == min(item.price_rub for item in last)
    assert {item.destination.iata for item in last} == {"SVO"}
    assert any(item.arrival_at.date() == date(2031, 1, 1) for item in last)


async def test_dst_duration_survives_d1_validation_and_calendar_selection():
    origin = Place(iata="BER", name="Berlin", latitude=52.4, longitude=13.5, timezone="Europe/Berlin")
    destination = Place(iata="MUC", name="Munich", latitude=48.3, longitude=11.8, timezone="Europe/Berlin")
    observed = []

    async def search(query):
        departure = datetime.combine(query.departure_date, datetime.min.time(), ZoneInfo(origin.timezone))
        departure = departure.replace(hour=1, minute=30)
        arrival = arrival_after(departure, 120, destination)
        flight = Flight(
            id=f"dst-integration-{query.departure_date}",
            origin=origin,
            destination=destination,
            departure_at=departure,
            arrival_at=arrival,
            price_rub=2000,
            carrier="Synthetic integration fixture",
            is_synthetic=True,
        )
        observed.append(flight)
        return [flight]

    result = await price_calendar(PriceCalendarQuery(origin="BER", destination="MUC", month="2030-03"), search)
    transition = observed[-1]
    assert transition.departure_at.utcoffset() == timedelta(hours=1)
    assert transition.arrival_at.utcoffset() == timedelta(hours=2)
    assert transition.arrival_at.hour == 4
    assert transition.arrival_at.astimezone(UTC) - transition.departure_at.astimezone(UTC) == timedelta(hours=2)
    assert result.days[-1].min_price_rub == 2000


@pytest.mark.parametrize("destination", ["AYT", "DXB", "MLE"])
async def test_full_quote_uses_same_hotel_all_nights_and_return_on_checkout_date(destination, monkeypatch):
    day, nights, seed = date(2026, 11, 15), 7, 777
    party = Travelers(adults=2, children_ages=[5])
    end = day + timedelta(days=nights)
    available = await search_hotel_results(
        HotelSearch(
            destination=destination,
            check_in=day,
            check_out=end,
            travelers=party,
            refundable_only=True,
        )
    )
    assert available
    hotel = available[0]
    calls = []
    original = u2_cli.generate_flights

    def record(query, *, seed):
        calls.append(query)
        return original(query, seed=seed)

    monkeypatch.setattr(u2_cli, "generate_flights", record)
    quote = await u2_cli.quote_demo_trip(
        day,
        hotel_id=hotel.hotel_id,
        destination=destination,
        nights=nights,
        travelers=party,
        seed=seed,
    )
    assert quote is not None
    assert [(query.origin, query.destination, query.departure_date) for query in calls] == [
        ("SVO", destination, day),
        (destination, "SVO", end),
    ]
    assert all(query.travelers == party for query in calls)
    out = [item for item in original(calls[0], seed=seed) if item.arrival_at.date() == day]
    back = original(calls[1], seed=seed)
    assert quote.flight_total_rub == sum(
        min(item.price_rub + item.baggage_price_rub for item in leg) for leg in (out, back)
    )
    assert quote.hotel_total_rub == hotel.offer.price_rub
    assert hotel.offer.check_in == day and hotel.offer.check_out == end
    assert hotel.total_nights == nights and hotel.rooms == 2
    assert quote.total_rub == quote.flight_total_rub + hotel.offer.price_rub
    assert quote.hotel_id == hotel.hotel_id and quote.travelers == party


async def test_night_only_outbound_and_absent_return_or_hotel_cannot_be_complete_quotes(monkeypatch):
    day = date(2026, 11, 15)
    party = Travelers(adults=2)
    hotels = await search_hotel_results(
        HotelSearch(
            destination="DXB",
            check_in=day,
            check_out=day + timedelta(days=7),
            travelers=party,
            refundable_only=True,
        )
    )
    original = u2_cli.generate_flights

    def night_only(query, *, seed):
        return [offer for offer in original(query, seed=seed) if offer.departure_at.hour == 23]

    kwargs = dict(hotel_id=hotels[0].hotel_id, destination="DXB", nights=7, travelers=party)
    monkeypatch.setattr(u2_cli, "generate_flights", night_only)
    assert await u2_cli.quote_demo_trip(day, **kwargs) is None

    def absent_return(query, *, seed):
        return [] if query.origin == "DXB" else original(query, seed=seed)

    monkeypatch.setattr(u2_cli, "generate_flights", absent_return)
    assert await u2_cli.quote_demo_trip(day, **kwargs) is None
    monkeypatch.setattr(u2_cli, "generate_flights", original)
    assert await u2_cli.quote_demo_trip(day, **{**kwargs, "hotel_id": "absent-physical-hotel"}) is None


async def test_date_saving_facts_reproduce_complete_same_hotel_group_quotes():
    day, seed = date(2026, 11, 15), 42
    party = Travelers(adults=2, children_ages=[5])
    hotels = await search_hotel_results(
        HotelSearch(
            destination="DXB",
            check_in=day,
            check_out=day + timedelta(days=7),
            travelers=party,
            refundable_only=True,
        )
    )
    hotel_id = hotels[0].hotel_id

    async def quote_day(candidate):
        return await u2_cli.quote_demo_trip(
            candidate,
            hotel_id=hotel_id,
            destination="DXB",
            nights=7,
            travelers=party,
            seed=seed,
        )

    selected = await quote_day(day)
    assert selected is not None
    facts = await suggest_date_shifts(selected, quote_day)
    expected = {}
    for offset in (-3, -2, -1, 1, 2, 3):
        candidate = await quote_day(day + timedelta(days=offset))
        if candidate is not None and (selected.total_rub - candidate.total_rub) * 20 > selected.total_rub:
            expected[offset] = selected.total_rub - candidate.total_rub
    assert {fact.shift_days: fact.saving_rub for fact in facts} == expected
    for fact in facts:
        candidate = await quote_day(fact.suggested_departure)
        assert candidate is not None
        assert fact.suggested_total_rub == candidate.total_rub
        assert fact.hotel_id == selected.hotel_id == candidate.hotel_id
        assert candidate.travelers == selected.travelers == party
        assert fact.saving_percent == (Decimal(fact.saving_rub) * 100 / selected.total_rub).quantize(Decimal("0.01"))


async def test_demo_runs_offline_and_seed_is_reproducible(monkeypatch):
    async def forbidden_network(*_args, **_kwargs):
        pytest.fail("Data demo must use saved sources; attempted an HTTP send")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_network)
    first = await u2_cli.demo_payload(seed=42)
    assert first == await u2_cli.demo_payload(seed=42)
    changed = await u2_cli.demo_payload(seed=43)
    assert first["flights"] != changed["flights"]
    assert first["calendar"] != changed["calendar"]
    assert first["selected_quote"]["flight_total_rub"] != changed["selected_quote"]["flight_total_rub"]
    assert first["demo"] is True and first["currency"]["is_historical_snapshot"] is True


async def test_strict_savings_threshold_is_independent_of_callers_decimal_context():
    selected = TripQuote(
        departure_date=date(2030, 11, 18),
        nights=7,
        origin="SVO",
        destination="DXB",
        hotel_id="same-hotel",
        travelers=Travelers(adults=2),
        flight_total_rub=60000,
        hotel_total_rub=40000,
        is_synthetic=True,
    )

    async def quote(day):
        return selected.model_copy(update={"departure_date": day, "flight_total_rub": 54999})

    # 5001/100000 is strictly more than 5%, even when a caller uses low precision.
    with localcontext() as context:
        context.prec = 2
        facts = await suggest_date_shifts(selected, quote, window_days=1)
    assert len(facts) == 2
    assert all(fact.saving_rub == 5001 for fact in facts)


async def test_demo_reports_unrepresentable_stay_as_input_error():
    with pytest.raises(ValueError, match="семи календарных дней"):
        await u2_cli.demo_payload(departure=date.max)


def test_cli_handles_calendar_boundary_without_a_traceback():
    result = subprocess.run(
        [sys.executable, "-m", "app.u2_cli", "--departure-date", "9999-12-31", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "семи календарных дней" in result.stderr
    assert "Traceback" not in result.stderr


def test_seed_is_process_independent_even_when_python_hash_seed_changes():
    code = (
        "import json;from datetime import date;from app.contracts.tools import FlightSearch;"
        "from app.tools.flights import generate_flights;"
        "print(json.dumps([x.model_dump(mode='json') for x in generate_flights("
        "FlightSearch(origin='SVO',destination='DXB',departure_date=date(2030,11,18)),seed=42)],sort_keys=True))"
    )
    results = [
        subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONHASHSEED": value},
        ).stdout
        for value in ("1", "98765")
    ]
    assert results[0] == results[1]
