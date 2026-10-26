"""The published S4 data must be consumable through the public D1 API."""

import json
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.booking.timeline import build_timeline
from app.booking.validator import validate_trip
from app.contracts.tools import Flight, HotelOffer, Place, Transfer, TransferRequest, Travelers
from app.tools.errors import UnsupportedTransfer
from app.tools.registry import run_tool
from app.tools.s4_adapter import MODE_MAP, S4TransferTool, transfer_leg
from app.tools.transfers import TransferBase

DATA = Path(__file__).resolve().parents[2] / "data"
CASES = json.loads((DATA / "transfers/cases.json").read_text())["cases"]
HOTELS = json.loads((DATA / "hotels/osm_hotels.json").read_text())["hotels"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
async def test_all_manual_s4_cases_pass_through_d1(case):
    tool = S4TransferTool()
    arguments = {key: case[key] for key in ("hotel_id", "airport", "pickup_at", "travelers")}
    result = await run_tool("get_transfer", arguments, tools={tool.name: tool})
    assert result
    expected = case["expect"]
    for key, value in expected.get("first", {}).items():
        if key == "mode":
            value = MODE_MAP.get(value, value)
        elif key in {"depart_at", "arrive_at"}:
            value = datetime.fromisoformat(value)
        actual = transfer_leg(result[0]).arrive_at if key == "arrive_at" else getattr(result[0], key)
        assert actual == value
    by_mode = {item.mode: item for item in result}
    for field in ("price_rub", "vehicles", "issues", "depart_at"):
        for mode, value in expected.get(field, {}).items():
            actual = getattr(by_mode[MODE_MAP.get(mode, mode)], field)
            if field == "depart_at":
                value = datetime.fromisoformat(value)
            assert actual == value
    for item in result:
        assert Transfer.model_validate_json(item.model_dump_json()) == item
        assert item.destination.id == case["hotel_id"]
        assert item.is_synthetic


@pytest.mark.parametrize("hotel", HOTELS, ids=lambda hotel: hotel["hotel_id"])
async def test_every_published_s4_hotel_and_option_resolves_without_llm_coordinates(hotel):
    request = TransferRequest(
        airport=hotel["destination"], hotel_id=hotel["hotel_id"], pickup_at="2026-11-18T09:00:00Z"
    )
    quotes = await S4TransferTool().run(request)
    options = TransferBase.load().get(hotel["hotel_id"], hotel["destination"]).options
    assert {quote.id for quote in quotes} == {option.id for option in options}
    by_id = {quote.id: quote for quote in quotes}
    for option in options:
        quote = by_id[option.id]
        assert quote.mode == MODE_MAP.get(option.mode, option.mode)
        assert quote.distance_km == option.distance_km
        assert quote.luggage_limit == option.luggage_limit
        assert quote.prebook_hours == option.prebook_hours
        assert quote.pets is option.pets and quote.child_seat is option.child_seat
        assert quote.destination.latitude == hotel["lat"]
        assert quote.destination.longitude == hotel["lon"]
        leg = transfer_leg(quote)
        assert leg.option_id == quote.id and leg.price_rub == quote.price_rub
        assert leg.issues == quote.issues
        assert leg.arrive_at > leg.depart_at


async def test_timezone_equivalent_requests_produce_identical_local_quotes():
    case = next(case for case in CASES if case["airport"] == "DXB")
    tool = S4TransferTool()
    base = {"airport": "DXB", "hotel_id": case["hotel_id"]}
    utc = await tool.run(TransferRequest(**base, pickup_at="2026-11-18T22:30:00Z"))
    moscow = await tool.run(TransferRequest(**base, pickup_at="2026-11-19T01:30:00+03:00"))
    assert utc == moscow
    assert utc[0].pickup_at.isoformat() == "2026-11-19T02:30:00+04:00"


async def test_unknown_or_mismatched_hotel_uses_common_error():
    from app.tools.transfers import UnsupportedTransfer as S4UnsupportedTransfer

    assert S4UnsupportedTransfer is UnsupportedTransfer
    tool = S4TransferTool()
    for airport, hotel_id in (("DXB", HOTELS[0]["hotel_id"]), ("AYT", "osm:node/999999999999")):
        with pytest.raises(UnsupportedTransfer):
            await tool.run(TransferRequest(airport=airport, hotel_id=hotel_id, pickup_at="2026-11-18T12:00:00Z"))


def test_model_does_not_need_to_supply_or_invent_place_facts():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TransferRequest(
            airport="AYT",
            hotel_id=HOTELS[0]["hotel_id"],
            pickup_at="2026-11-18T12:00:00Z",
            latitude=0,
        )


def test_timeline_bridge_duration_is_elapsed_time_across_dst_change():
    origin = Place(kind="airport", iata="BER", name="Berlin", latitude=52.36, longitude=13.5, timezone="Europe/Berlin")
    departure = datetime(2030, 3, 31, 1, 30, tzinfo=ZoneInfo("Europe/Berlin"))
    transfer = Transfer(
        id="dst",
        origin=origin,
        destination=origin.model_dump() | {"kind": "hotel", "id": "demo-berlin-hotel"},
        pickup_at=departure,
        depart_at=departure,
        hours=None,
        available=True,
        duration_minutes=120,
        price_rub=1000,
        mode="taxi",
        is_synthetic=True,
    )
    leg = transfer_leg(transfer)
    assert leg.arrive_at.isoformat() == "2030-03-31T04:30:00+02:00"
    assert leg.arrive_at.timestamp() - leg.depart_at.timestamp() == 120 * 60


async def test_male_night_quote_reaches_s3_s5_with_baggage_and_prebooking():
    party = Travelers(adults=2)
    quote = (
        await S4TransferTool().run(
            TransferRequest(
                airport="MLE", hotel_id="osm:node/4455235191", pickup_at="2026-11-18T18:40:00Z", travelers=party
            )
        )
    )[0]
    leg = transfer_leg(quote)
    outbound = Flight(
        id="night",
        origin=Place(
            kind="airport", iata="SVO", name="Moscow", latitude=55.97, longitude=37.41, timezone="Europe/Moscow"
        ),
        destination=quote.origin,
        departure_at="2026-11-18T14:30:00+03:00",
        arrival_at="2026-11-18T22:55:00+05:00",
        price_rub=80000,
        carrier="XX",
        is_synthetic=True,
    )
    stay = HotelOffer(
        hotel_id=quote.destination.id,
        offer_id="night-offer",
        name=quote.destination.name,
        place=quote.destination,
        check_in=date(2026, 11, 18),
        check_out=date(2026, 11, 25),
        check_in_time=time(14),
        check_out_time=time(12),
        price_rub=100000,
        is_synthetic=True,
    )
    timeline = build_timeline(outbound, stay, arrival_transfer=leg)
    assert timeline.wait("airport_after_arrival").minutes == 380
    assert quote.price_rub == leg.price_rub == 66000
    issues = validate_trip(timeline, stay, party, arrival_transfer=leg, airport="MLE", base=TransferBase.load())
    by_rule = {issue.rule: issue for issue in issues}
    assert by_rule["airport_overnight_wait"].severity == "critical"
    assert "20" in by_rule["transfer_luggage"].message
    assert "72" in by_rule["transfer_prebook"].message
