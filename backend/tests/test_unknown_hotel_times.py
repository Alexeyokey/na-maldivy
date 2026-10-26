"""Unknown hotel hours must survive S3/S5 without fabricated waits or crashes."""

from datetime import date, time

import pytest

from app.booking.timeline import build_timeline
from app.booking.validator import validate_trip
from app.contracts.tools import Flight, HotelOffer, Place, Travelers
from app.tools.registry import run_tool


@pytest.mark.parametrize("check_in_time,check_out_time", [(None, None), (None, time(12)), (time(14), None)])
@pytest.mark.parametrize("with_return", [False, True])
def test_partial_unknown_hours_keep_other_trip_rules_active(check_in_time, check_out_time, with_return):
    airport = Place(
        kind="airport", iata="AYT", name="Antalya", latitude=36.9, longitude=30.8, timezone="Europe/Istanbul"
    )
    flight = Flight(
        id="f1",
        origin=Place(
            kind="airport", iata="SVO", name="Moscow", latitude=55.97, longitude=37.41, timezone="Europe/Moscow"
        ),
        destination=airport,
        departure_at="2026-11-18T01:00:00+03:00",
        arrival_at="2026-11-18T05:00:00+03:00",
        price_rub=30000,
        carrier="XX",
        is_synthetic=True,
    )
    stay = HotelOffer(
        hotel_id="osm:node/3623376904",
        offer_id="offer",
        name="Hotel",
        place=airport.model_dump() | {"kind": "hotel", "id": "osm:node/3623376904"},
        check_in=date(2026, 11, 18),
        check_out=date(2026, 11, 25),
        check_in_time=check_in_time,
        check_out_time=check_out_time,
        price_rub=70000,
        adults_only=True,
        is_synthetic=True,
    )
    inbound = None
    if with_return:
        inbound = Flight.model_validate(
            flight.model_dump()
            | {
                "id": "return",
                "origin": airport,
                "destination": flight.origin,
                "departure_at": "2026-11-25T18:00:00+03:00",
                "arrival_at": "2026-11-25T22:00:00+03:00",
            }
        )
    timeline = build_timeline(flight, stay, inbound)
    assert (timeline.booked_check_in is None) == (check_in_time is None)
    assert (timeline.booked_check_out is None) == (check_out_time is None)
    if check_in_time is None:
        assert timeline.event("check_in") is None
        assert timeline.wait("hotel_before_check_in") is None
    if check_out_time is None:
        assert timeline.event("check_out") is None
    if with_return:
        assert timeline.event("return_departure") is not None
    issues = validate_trip(timeline, stay, Travelers(adults=2, children_ages=[5]))
    assert any(issue.rule == "adults_only_with_children" and issue.severity == "critical" for issue in issues)
    unknown = next(issue for issue in issues if issue.rule == "hotel_times_unknown")
    assert unknown.fixes[0].price_delta_rub is None
    assert set(unknown.fixes[0].patch["fields"]) == {
        name for name, value in (("check_in_time", check_in_time), ("check_out_time", check_out_time)) if value is None
    }


async def test_not_24h_reception_with_unknown_closing_warns_on_night_arrival():
    flight = (
        await run_tool("search_flights", {"origin": "SVO", "destination": "AYT", "departure_date": "2026-11-18"})
    )[1]
    stay = (
        await run_tool("search_hotels", {"destination": "AYT", "check_in": "2026-11-19", "check_out": "2026-11-26"})
    )[0]
    stay.reception_24h = False
    stay.reception_closes_at = None
    stay = HotelOffer.model_validate(stay)
    issues = validate_trip(build_timeline(flight, stay), stay, Travelers())
    reception = next(issue for issue in issues if issue.rule == "reception_closed")
    assert reception.severity == "warning"
    assert "неизвестно" in reception.message
