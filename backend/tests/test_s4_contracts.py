"""Cross-team identities, unknown facts and local transfer schedules."""

import json
from datetime import time
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts.identifiers import canonical_hotel_id
from app.contracts.tools import HotelOffer, Place, Transfer, TransferHours
from app.tools.errors import UnsupportedTransfer

AIRPORT = {
    "kind": "airport",
    "iata": "MLE",
    "name": "Velana",
    "latitude": 4.19,
    "longitude": 73.53,
    "timezone": "Indian/Maldives",
}
HOTEL = AIRPORT | {"kind": "hotel", "id": "osm:way/123456", "name": "Demo Resort"}


def hotel(**changes: Any) -> HotelOffer:
    return HotelOffer.model_validate(
        {
            "hotel_id": "osm:way/123456",
            "offer_id": "offer-november-family",
            "name": "Demo Resort",
            "place": HOTEL,
            "check_in": "2026-11-16",
            "check_out": "2026-11-23",
            "price_rub": 210000,
            "is_synthetic": True,
        }
        | changes
    )


def transfer(**changes: Any) -> Transfer:
    return Transfer.model_validate(
        {
            "id": "seaplane-1",
            "origin": AIRPORT,
            "destination": HOTEL,
            "pickup_at": "2026-11-15T23:40:00+05:00",
            "hours": {"from": "06:00", "to": "18:00"},
            "available": False,
            "depart_at": "2026-11-16T06:00:00+05:00",
            "vehicles": 1,
            "duration_minutes": 40,
            "price_rub": 42000,
            "mode": "seaplane",
            "is_synthetic": True,
        }
        | changes
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("osm:node/123", "osm:node/123"),
        ("osm:way:123456", "osm:way/123456"),
        ("osm:relation:555", "osm:relation/555"),
        ("demo-ayt-family", "demo-ayt-family"),
        ("provider:hotel_17.v2", "provider:hotel_17.v2"),
    ],
)
def test_hotel_identity_normalization(value: str, expected: str) -> None:
    assert canonical_hotel_id(value) == expected
    assert canonical_hotel_id(expected) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        123,
        "",
        " ",
        " hotel-1",
        "hotel 1",
        "osm:way/0",
        "osm:node/-1",
        "osm:foo/123",
        "osm:way/123/more",
        "osm:node/001",
        "OSM:way/123",
        "../hotel",
        "a/../b",
        "hotel\\file",
        "hotel%2Ffile",
        "hotel..file",
        "https://example.org/hotel/1",
    ],
)
def test_invalid_or_path_like_id_rejected(value: Any) -> None:
    with pytest.raises(ValueError):
        canonical_hotel_id(value)


def test_hotel_place_requires_kind_and_persistent_id() -> None:
    assert Place.model_validate(HOTEL | {"id": "osm:way:123456"}).id == "osm:way/123456"
    for value in [AIRPORT | {"kind": "hotel"}, HOTEL | {"id": None}, HOTEL | {"id": "../escape"}]:
        with pytest.raises(ValidationError):
            Place.model_validate(value)
    with pytest.raises(ValidationError):
        Place.model_validate({key: value for key, value in AIRPORT.items() if key != "kind"})
    assert Place.model_validate(AIRPORT).id is None


def test_offer_alias_is_input_only_and_persistent_identity_is_separate() -> None:
    data = hotel().model_dump()
    alias = HotelOffer.model_validate(
        {key: value for key, value in data.items() if key != "offer_id"} | {"id": "legacy"}
    )
    assert alias.offer_id == alias.id == "legacy"
    assert alias.hotel_id == "osm:way/123456"
    assert json.loads(alias.model_dump_json())["offer_id"] == "legacy"
    assert "id" not in json.loads(alias.model_dump_json())
    with pytest.raises((ValueError, AttributeError)):
        alias.id = "replacement"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        HotelOffer.model_validate(data | {"id": "ambiguous"})
    for field in ("hotel_id", "offer_id"):
        with pytest.raises(ValidationError):
            HotelOffer.model_validate({key: value for key, value in data.items() if key != field})


def test_hotel_and_place_ids_normalize_before_consistency_check() -> None:
    result = hotel(hotel_id="osm:way:123456", place=HOTEL | {"id": "osm:way:123456"})
    assert result.hotel_id == result.place.id == "osm:way/123456"
    with pytest.raises(ValidationError, match="must equal hotel_id"):
        hotel(place=HOTEL | {"id": "osm:node/999"})
    with pytest.raises(ValidationError, match="kind=hotel"):
        hotel(place=AIRPORT)


def test_unknown_hotel_facts_are_not_false_free_or_round_the_clock() -> None:
    result = hotel()
    assert result.stars is result.rating is result.adults_only is result.pets_allowed is None
    assert result.refundable is result.reception_24h is result.reception_closes_at is None
    assert result.early_check_in_available is result.early_check_in_price_rub is None
    assert hotel(early_check_in_available=True, early_check_in_price_rub=0).early_check_in_price_rub == 0
    assert hotel(early_check_in_available=True).early_check_in_price_rub is None
    assert hotel(reception_24h=False).reception_closes_at is None
    assert hotel(reception_24h=True).reception_closes_at is None
    assert hotel(reception_24h=False, reception_closes_at="22:00").reception_closes_at == time(22)


@pytest.mark.parametrize(
    "changes",
    [
        {"stars": 0},
        {"stars": 6},
        {"stars": True},
        {"stars": 4.0},
        {"rating": -1},
        {"rating": 11},
        {"rating": float("nan")},
        {"rating": float("inf")},
        {"reception_24h": True, "reception_closes_at": "22:00"},
        {"reception_closes_at": "22:00"},
        {"early_check_in_available": False, "early_check_in_price_rub": 0},
        {"early_check_in_price_rub": 1000},
        {"early_check_in_available": True, "early_check_in_price_rub": -1},
        {"check_in_time": "14:00+05:00"},
        {"check_out_time": "12:00Z"},
    ],
)
def test_contradictory_or_invalid_hotel_facts_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        hotel(**changes)


@pytest.mark.parametrize(
    "field", ["adults_only", "pets_allowed", "refundable", "reception_24h", "early_check_in_available"]
)
def test_hotel_tri_state_rejects_strings_and_numbers(field: str) -> None:
    assert getattr(hotel(**{field: False}), field) is False
    assert getattr(hotel(**{field: True}), field) is True
    with pytest.raises(ValidationError):
        hotel(**{field: "false"})
    with pytest.raises(ValidationError):
        hotel(**{field: 0})


def test_transfer_window_alias_serializes_everywhere_and_forbids_equal_bounds() -> None:
    hours = TransferHours(from_="06:00", to="18:00")
    assert hours == TransferHours.model_validate({"from": "06:00", "to": "18:00"})
    assert hours.model_dump(mode="json") == {"from": "06:00:00", "to": "18:00:00"}
    assert json.loads(transfer().model_dump_json())["hours"] == {"from": "06:00:00", "to": "18:00:00"}
    for kwargs in [{"from_": "00:00", "to": "00:00"}, {"from_": "06:00+05:00", "to": "18:00"}]:
        with pytest.raises(ValidationError):
            TransferHours(**kwargs)


@pytest.mark.parametrize(
    "start,end,clock,inside",
    [
        (6, 18, time(6), True),
        (6, 18, time(17, 59), True),
        (6, 18, time(18), False),
        (22, 6, time(23, 40), True),
        (22, 6, time(0), True),
        (22, 6, time(5, 59), True),
        (22, 6, time(6), False),
        (22, 6, time(12), False),
    ],
)
def test_daily_windows_include_opening_exclude_closing_and_cross_midnight(
    start: int,
    end: int,
    clock: time,
    inside: bool,
) -> None:
    assert TransferHours(from_=time(start), to=time(end)).contains(clock) is inside


def test_late_arrival_waits_for_next_day_and_keeps_requested_pickup() -> None:
    result = transfer()
    assert result.pickup_at.date().isoformat() == "2026-11-15"
    assert result.depart_at.date().isoformat() == "2026-11-16"
    assert not result.available and result.depart_at > result.pickup_at
    assert result.pets is result.child_seat is None
    assert result.price_rub == 42000 and result.vehicles == 1
    assert transfer(available=False, depart_at=None).depart_at is None
    assert transfer(hours=None, available=False, depart_at=None).hours is None


def test_available_refers_to_schedule_not_pet_or_child_seat_eligibility() -> None:
    now = "2026-11-15T23:40:00+05:00"
    result = transfer(hours=None, available=True, depart_at=now, pets=False, child_seat=None)
    assert result.available is True and result.pets is False and result.child_seat is None
    result = transfer(hours={"from": "22:00", "to": "06:00"}, available=True, depart_at=now)
    assert result.available is True


def test_next_scheduled_departure_can_be_later_inside_an_open_window() -> None:
    result = transfer(pickup_at="2026-11-15T09:00:00+05:00", depart_at="2026-11-15T09:30:00+05:00")
    assert result.available is False


@pytest.mark.parametrize(
    "changes",
    [
        {"available": True},
        {"available": True, "depart_at": None},
        {"depart_at": "2026-11-15T23:40:00+05:00"},
        {"depart_at": "2026-11-15T06:00:00+05:00"},
        {"depart_at": "2026-11-16T18:00:00+05:00"},
        {"depart_at": "2026-11-16T06:00:00+04:00"},
        {"depart_at": "2026-11-16T06:00:00"},
        {"vehicles": 0},
        {"vehicles": 1.0},
        {"vehicles": True},
        {"available": "false"},
        {"available": 1},
        {"pets": "false"},
        {"child_seat": 0},
        {"price_rub": 42000.0},
    ],
)
def test_transfer_rejects_impossible_schedule_and_non_strict_values(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        transfer(**changes)


@pytest.mark.parametrize("field", ["hours", "available", "depart_at"])
def test_transfer_schedule_fields_are_required_even_if_nullable(field: str) -> None:
    data = transfer().model_dump()
    with pytest.raises(ValidationError):
        Transfer.model_validate({key: value for key, value in data.items() if key != field})


@pytest.mark.parametrize("mode", ["taxi", "minivan", "shuttle", "bus", "metro", "boat", "seaplane"])
def test_s4_vehicle_modes_and_group_price(mode: str) -> None:
    assert transfer(mode=mode).mode == mode


def test_dst_offset_checked_for_future_departure_in_origin_zone() -> None:
    berlin = AIRPORT | {"iata": "BER", "timezone": "Europe/Berlin"}
    result = transfer(
        origin=berlin,
        pickup_at="2026-03-29T01:30:00+01:00",
        depart_at="2026-03-29T03:00:00+02:00",
        hours={"from": "03:00", "to": "18:00"},
    )
    assert result.depart_at.utcoffset().total_seconds() == 7200
    with pytest.raises(ValidationError, match="origin's local UTC offset"):
        transfer(origin=berlin, pickup_at="2026-03-29T01:30:00+01:00", depart_at="2026-03-29T03:00:00+01:00")


def test_shared_unsupported_transfer_preserves_valueerror_compatibility() -> None:
    assert issubclass(UnsupportedTransfer, ValueError)
