"""Проверки общих договорённостей O1 на границах travel-инструментов."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts.tools import (
    DayPrice,
    DestinationQuery,
    Flight,
    HotelOffer,
    HotelSearch,
    Place,
    PriceCalendarQuery,
    Transfer,
    Travelers,
)

SVO = {
    "kind": "airport",
    "iata": "SVO",
    "name": "Шереметьево",
    "latitude": 55.97,
    "longitude": 37.41,
    "timezone": "Europe/Moscow",
}
DXB = {
    "kind": "airport",
    "iata": "DXB",
    "name": "Дубай",
    "latitude": 25.25,
    "longitude": 55.36,
    "timezone": "Asia/Dubai",
}


def flight(**changes: Any) -> Flight:
    data = {
        "id": "flight-1",
        "origin": SVO,
        "destination": DXB,
        "departure_at": "2026-11-15T23:30:00+03:00",
        "arrival_at": "2026-11-16T05:30:00+04:00",
        "price_rub": 42000,
        "carrier": "Demo Air",
        "is_synthetic": True,
    }
    return Flight.model_validate(data | changes)


@pytest.mark.parametrize("value", [True, 100.0, "100", -1])
def test_rubles_require_nonnegative_integers(value: Any) -> None:
    with pytest.raises(ValidationError):
        DayPrice(date="2026-11-15", price_rub=value, is_synthetic=True)


def test_zero_rubles_and_integer_json_round_trip() -> None:
    original = DayPrice(date="2026-11-15", price_rub=0, is_synthetic=True)

    assert DayPrice.model_validate_json(original.model_dump_json()) == original
    assert type(original.model_dump(mode="json")["price_rub"]) is int


def test_flight_rejects_naive_times_and_wrong_local_offset() -> None:
    with pytest.raises(ValidationError):
        flight(departure_at="2026-11-15T23:30:00")
    with pytest.raises(ValidationError, match="UTC-смещение"):
        flight(arrival_at="2026-11-16T05:30:00+03:00")


def test_flight_orders_actual_instants_across_midnight() -> None:
    # 00:15 в Дубае — 20:15 UTC, раньше вылета в 20:30 UTC.
    with pytest.raises(ValidationError, match="позже отправления"):
        flight(arrival_at="2026-11-16T00:15:00+04:00")


def test_arrival_wall_clock_can_be_earlier_than_departure() -> None:
    result = flight(
        origin=SVO | {"iata": "LHR", "timezone": "Europe/London"},
        destination=DXB | {"iata": "JFK", "timezone": "America/New_York"},
        departure_at="2026-11-15T12:00:00+00:00",
        arrival_at="2026-11-15T10:00:00-05:00",
    )

    assert result.arrival_at > result.departure_at
    assert result.arrival_at.hour < result.departure_at.hour


@pytest.mark.parametrize("timezone", ["America/Unknown_City", "../etc/passwd", ""])
def test_place_requires_known_iana_timezone(timezone: str) -> None:
    with pytest.raises(ValidationError):
        Place.model_validate(SVO | {"timezone": timezone})


@pytest.mark.parametrize("check_out", ["2026-11-15", "2026-11-14"])
def test_hotel_search_and_offer_require_positive_stay(check_out: str) -> None:
    dates = {"check_in": "2026-11-15", "check_out": check_out}
    with pytest.raises(ValidationError, match="позже даты заезда"):
        HotelSearch(destination="DXB", **dates)
    with pytest.raises(ValidationError, match="позже даты заезда"):
        HotelOffer(
            id="hotel-1",
            hotel_id="hotel-1",
            name="Demo Hotel",
            place=DXB | {"kind": "hotel", "id": "hotel-1"},
            price_rub=50000,
            stars=4,
            rating=8.5,
            is_synthetic=True,
            **dates,
        )


@pytest.mark.parametrize("month", ["2026-00", "2026-13", "2026-1", "0000-11"])
def test_price_calendar_requires_real_iso_month(month: str) -> None:
    with pytest.raises(ValidationError):
        PriceCalendarQuery(origin="SVO", destination="DXB", month=month)


@pytest.mark.parametrize("changes", [{"adults": 0}, {"children_ages": [-1]}, {"children_ages": [18]}, {"pets": -1}])
def test_travelers_reject_impossible_party(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Travelers.model_validate(changes)


def test_contracts_reject_misspelled_arguments() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Travelers.model_validate({"childen_ages": [5]})


def test_destination_origin_is_required_instead_of_silently_assuming_moscow() -> None:
    with pytest.raises(ValidationError, match="origin"):
        DestinationQuery(month=11)
    result = DestinationQuery(month=11, origin="LED")
    assert result.origin == "LED"
    assert "origin" in DestinationQuery.model_json_schema()["required"]


def test_transfer_output_rejects_nonlocal_pickup() -> None:
    with pytest.raises(ValidationError, match="местным"):
        Transfer.model_validate(
            {
                "id": "transfer-1",
                "origin": DXB,
                "destination": DXB | {"latitude": 25.26},
                "pickup_at": "2026-11-16T12:00:00+03:00",
                "hours": None,
                "available": True,
                "depart_at": "2026-11-16T12:00:00+03:00",
                "duration_minutes": 20,
                "price_rub": 1000,
                "is_synthetic": True,
            }
        )
