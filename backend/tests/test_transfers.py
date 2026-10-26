import json
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.tools.transfers import (
    DEFAULT_PATH,
    HotelTransfers,
    Hours,
    TransferBase,
    Travelers,
    UnsupportedTransfer,
    quote_transfers,
)

DATA = DEFAULT_PATH.parent
CASES = json.loads((DATA / "cases.json").read_text(encoding="utf-8"))["cases"]
HOTELS = json.loads((DATA.parent / "hotels" / "osm_hotels.json").read_text(encoding="utf-8"))["hotels"]
DUBAI = ZoneInfo("Asia/Dubai")


def record(**option: Any) -> HotelTransfers:
    """Отель с одним вариантом трансфера для проверки логики без настоящей базы."""
    base_option = {
        "id": "h:DXB:taxi",
        "mode": "taxi",
        "duration_min": 30,
        "distance_km": 20,
        "pricing": {"type": "per_vehicle", "price_rub": 2000, "capacity": 4},
        "hours": None,
        "luggage_limit": None,
        "child_seat": None,
        "pets": None,
        "prebook_hours": 0,
        "source": "synthetic",
        "labels": [],
    }
    return HotelTransfers.model_validate(
        {"hotel_id": "h", "airport": "DXB", "tz": "Asia/Dubai", "labels": [], "options": [base_option | option]}
    )


def quote(rec: HotelTransfers, at: str, **travelers: Any):  # noqa: ANN201
    pickup = datetime.fromisoformat(at).replace(tzinfo=DUBAI)
    return quote_transfers("h", "DXB", pickup, Travelers(**travelers), base=TransferBase([rec]))[0]


@pytest.mark.parametrize(
    ("start", "end", "inside", "outside"),
    [
        ("06:00", "15:30", ["06:00", "15:29"], ["05:59", "15:30", "23:40"]),
        ("05:00", "00:00", ["05:00", "23:59"], ["00:00", "04:59"]),  # до полуночи
        ("22:00", "06:00", ["22:00", "00:30", "05:59"], ["06:00", "21:59"]),  # через полночь
    ],
)
def test_hours_contains(start: str, end: str, inside: list[str], outside: list[str]) -> None:
    hours = Hours.model_validate({"from": start, "to": end})
    assert all(hours.contains(time.fromisoformat(t)) for t in inside)
    assert not any(hours.contains(time.fromisoformat(t)) for t in outside)


def test_unavailable_option_departs_at_next_opening() -> None:
    rec = record(hours={"from": "06:00", "to": "15:30"})

    late = quote(rec, "2026-11-18T23:40")
    early = quote(rec, "2026-11-19T03:00")

    assert not late.available
    assert late.depart_at == datetime(2026, 11, 19, 6, 0, tzinfo=DUBAI)
    assert late.arrive_at == datetime(2026, 11, 19, 6, 30, tzinfo=DUBAI)
    assert "unavailable_at_pickup" in late.issues
    assert early.depart_at == datetime(2026, 11, 19, 6, 0, tzinfo=DUBAI)


def test_pickup_is_converted_to_local_time() -> None:
    rec = record(hours={"from": "06:00", "to": "15:30"})
    moscow_pickup = datetime(2026, 11, 19, 5, 30, tzinfo=ZoneInfo("Europe/Moscow"))  # 06:30 в Дубае

    q = quote_transfers("h", "DXB", moscow_pickup, Travelers(), base=TransferBase([rec]))[0]

    assert q.available
    assert q.depart_at == datetime(2026, 11, 19, 6, 30, tzinfo=DUBAI)


def test_per_vehicle_price_counts_vehicles_and_night() -> None:
    night = {"night_multiplier": 1.3, "night_hours": {"from": "00:00", "to": "06:00"}}
    rec = record(pricing={"type": "per_vehicle", "price_rub": 2000, "capacity": 4, **night})

    day = quote(rec, "2026-11-18T12:00", adults=2)
    group_at_night = quote(rec, "2026-11-18T02:00", adults=4, children_ages=[7])

    assert (day.price_rub, day.vehicles, day.issues) == (2000, 1, [])
    assert (group_at_night.price_rub, group_at_night.vehicles) == (5200, 2)
    assert {"extra_vehicle", "night_surcharge", "child_seat_unknown"} <= set(group_at_night.issues)


def test_per_person_price_by_age() -> None:
    rec = record(mode="seaplane", pricing={"type": "per_person", "price_rub": 40000, "child_price_rub": 20000})

    q = quote(rec, "2026-11-18T10:00", adults=2, children_ages=[1, 5, 14])

    assert q.price_rub == 2 * 40000 + 0 + 20000 + 40000
    assert "child_seat_unknown" not in q.issues  # кресло важно только в машине


@pytest.mark.parametrize(
    ("pets", "child_seat", "travelers", "expected"),
    [
        (False, None, {"pets": 1}, "pets_forbidden"),
        (None, None, {"pets": 1}, "pets_unknown"),
        (None, False, {"children_ages": [3]}, "child_seat_missing"),
        (None, None, {"children_ages": [3]}, "child_seat_unknown"),
    ],
)
def test_unknown_is_not_no(pets: bool | None, child_seat: bool | None, travelers: dict, expected: str) -> None:
    assert expected in quote(record(pets=pets, child_seat=child_seat), "2026-11-18T12:00", **travelers).issues


def test_forbidden_options_go_last() -> None:
    rec = HotelTransfers.model_validate(
        {
            **record().model_dump(by_alias=True),
            "options": [
                {**record(pets=False).options[0].model_dump(by_alias=True), "id": "cheap"},
                {
                    **record(pets=True).options[0].model_dump(by_alias=True),
                    "id": "pricey",
                    "pricing": {"type": "per_vehicle", "price_rub": 5000, "capacity": 4},
                },
            ],
        }
    )

    quotes = quote_transfers(
        "h", "DXB", datetime(2026, 11, 18, 12, tzinfo=DUBAI), Travelers(pets=1), base=TransferBase([rec])
    )

    assert [q.option_id for q in quotes] == ["pricey", "cheap"]


def test_unknown_hotel_and_naive_time_raise() -> None:
    base = TransferBase([record()])
    with pytest.raises(UnsupportedTransfer):
        quote_transfers("nope", "DXB", datetime(2026, 11, 18, 12, tzinfo=DUBAI), Travelers(), base=base)
    with pytest.raises(ValueError, match="часовым поясом"):
        quote_transfers("h", "DXB", datetime(2026, 11, 18, 12), Travelers(), base=base)


# --- Настоящая база data/transfers/transfers.json


def test_every_hotel_has_transfers() -> None:
    base = TransferBase.load()
    hotel_ids = {h["hotel_id"] for h in HOTELS}
    records = list(base)

    assert len(hotel_ids) == len(HOTELS) == 60
    assert {r.hotel_id for r in records} == hotel_ids
    for dest in ("AYT", "DXB", "MLE"):
        assert sum(r.airport == dest for r in records) == 20


def test_labels_match_fields() -> None:
    """Метки в базе соответствуют определениям из data/transfers/README.md."""
    for rec in TransferBase.load():
        for o in rec.options:
            p = o.pricing
            expected = {
                "long_transfer": o.duration_min > 90,
                "no_night_service": o.hours is not None,
                "daylight_only": o.mode == "seaplane",
                "luggage_limit": o.luggage_limit is not None,
                "prebook_required": o.prebook_hours >= 24,
                "no_child_seat": o.child_seat is False,
                "pets_forbidden": o.pets is False,
                "small_capacity": p.type == "per_vehicle" and (p.capacity or 0) <= 4,
                "night_surcharge": p.night_multiplier > 1,
                "multi_leg": o.mode == "flight_speedboat",
            }
            assert set(o.labels) == {label for label, hit in expected.items() if hit}, o.id
        assert ("only_scheduled_transfer" in rec.labels) == all(o.hours is not None for o in rec.options)
        assert ("far_from_airport" in rec.labels) == (min(o.duration_min for o in rec.options) > 90)


def test_demo_traps_are_present() -> None:
    labels = [label for rec in TransferBase.load() for label in rec.labels]
    assert labels.count("only_scheduled_transfer") >= 5  # ночной прилёт на Мальдивы
    assert labels.count("far_from_airport") >= 4  # Аланья и дальние атоллы


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_reference_cases(case: dict[str, Any]) -> None:
    base = TransferBase.load()
    expect = case["expect"]
    quotes = quote_transfers(
        case["hotel_id"],
        case["airport"],
        datetime.fromisoformat(case["pickup_at"]),
        Travelers(**case["travelers"]),
        base=base,
    )
    by_mode = {q.mode: q for q in quotes}

    if "hotel_labels" in expect:
        assert base.get(case["hotel_id"], case["airport"]).labels == expect["hotel_labels"]
    if "modes" in expect:
        assert set(by_mode) == set(expect["modes"])
    for field, value in expect.get("first", {}).items():
        actual = getattr(quotes[0], field)
        assert (actual.isoformat() if isinstance(actual, datetime) else actual) == value, field
    for mode, depart_at in expect.get("depart_at", {}).items():
        assert by_mode[mode].depart_at.isoformat() == depart_at, mode
    for mode, issues in expect.get("issues", {}).items():
        assert by_mode[mode].issues == issues, mode
    for mode, price in expect.get("price_rub", {}).items():
        assert by_mode[mode].price_rub == price, mode
    for mode, vehicles in expect.get("vehicles", {}).items():
        assert by_mode[mode].vehicles == vehicles, mode


def test_cases_cover_every_issue_and_destination() -> None:
    assert {c["airport"] for c in CASES} == {"AYT", "DXB", "MLE"}
    covered = {issue for c in CASES for issues in c["expect"].get("issues", {}).values() for issue in issues}
    assert covered >= {
        "unavailable_at_pickup",
        "extra_vehicle",
        "night_surcharge",
        "pets_forbidden",
        "pets_unknown",
        "child_seat_unknown",
        "long_transfer",
        "prebook_required",
        "luggage_limit",
    }
    assert Path(DATA / "README.md").exists()
