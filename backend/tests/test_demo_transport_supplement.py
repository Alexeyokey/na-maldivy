"""Supplemental D5 coverage is explicit, reproducible and honest about its source."""

import hashlib
import json
import math
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.contracts.tools import FlightSearch, PriceCalendarQuery, TransferRequest, Travelers
from app.tools.demo_flights import DemoFlightTool
from app.tools.demo_transport import build_routes, build_transfers, canonical_json
from app.tools.destinations import load_catalogue
from app.tools.errors import UnsupportedTransfer
from app.tools.flights import FlightTool, group_fare
from app.tools.http_cache import CachedHttpClient, CacheMiss
from app.tools.price_calendar import price_calendar
from app.tools.s4_adapter import S4TransferTool, transfer_leg
from app.tools.transfers import TransferBase

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DESTINATIONS = frozenset(row.iata for row in load_catalogue(DATA / "destinations.yaml").destinations)
HOTELS = json.loads((DATA / "demo/hotels.generated.json").read_text(encoding="utf-8"))["hotels"]
ORIGINAL_HASHES = {
    "routes/network.json": "99f2df3b1669fc51cbf811ecc75c69678a537cc4b57b73258e432d53dc5bf7b9",
    "transfers/airports.json": "478fc65551f98ff3d7b5d5fe9970c5e067238c3c67d0f082eba9d81dc9cd6710",
    "transfers/transfers.json": "34b405d9257fa57b71c1f223c8b9f74c615950c7247eba1e44b45cce17d6c670",
    "hotels/osm_hotels.json": "f7ed86557f2f1bf4c29a09c86edf66befb886e7a137dca5aa65b77a5b82771e9",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail("Supplemental demo transport must not access the network")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


def test_supplements_are_reproducible_and_preserve_the_four_original_snapshots():
    before = {name: hashlib.sha256((DATA / name).read_bytes()).hexdigest() for name in ORIGINAL_HASHES}
    assert before == ORIGINAL_HASHES
    assert (DATA / "demo/routes.generated.json").read_bytes() == canonical_json(build_routes(DATA)).encode("utf-8")
    assert (DATA / "demo/transfers.generated.json").read_bytes() == canonical_json(build_transfers(DATA)).encode(
        "utf-8"
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_demo_transport.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert {name: hashlib.sha256((DATA / name).read_bytes()).hexdigest() for name in ORIGINAL_HASHES} == before


@pytest.mark.parametrize("name", ["routes.generated.json", "transfers.generated.json"])
def test_generator_check_rejects_crlf_byte_drift_and_generation_restores_canonical_bytes(tmp_path, name):
    for relative in (
        "destinations.yaml",
        "routes/network.json",
        "demo/hotels.generated.json",
        "demo/routes.generated.json",
        "demo/transfers.generated.json",
    ):
        output = tmp_path / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DATA / relative, output)
    target = tmp_path / "demo" / name
    canonical = target.read_bytes()
    target.write_bytes(canonical.replace(b"\n", b"\r\n"))
    command = [sys.executable, str(ROOT / "scripts/build_demo_transport.py"), "--data-dir", str(tmp_path)]
    rejected = subprocess.run([*command, "--check"], capture_output=True, text=True, check=False)
    assert rejected.returncode == 1 and name in rejected.stderr
    assert target.read_bytes() != canonical, "--check must not repair the artifact silently"
    regenerated = subprocess.run(command, capture_output=True, text=True, check=False)
    assert regenerated.returncode == 0, regenerated.stderr
    assert target.read_bytes() == canonical


def test_route_supplement_contains_only_89_missing_routes_and_discloses_nonstop_assumption():
    original = json.loads((DATA / "routes/network.json").read_text(encoding="utf-8"))
    supplement = json.loads((DATA / "demo/routes.generated.json").read_text(encoding="utf-8"))
    old_routes = {(row["origin"], row["destination"]) for row in original["routes"]}
    new_routes = {(row["origin"], row["destination"]) for row in supplement["routes"]}
    assert len(new_routes) == len(supplement["routes"]) == 89
    assert not old_routes.intersection(new_routes)
    assert old_routes | new_routes == {
        (origin, destination) for origin in ("SVO", "LED") for destination in DESTINATIONS if origin != destination
    }
    assert supplement["demo_source"] == "deterministic_mock"
    assert "not checked" in supplement["sources"]["deterministic_mock"]["availability"]
    assert all(row["demo_source"] == "deterministic_mock" for row in supplement["routes"])


@pytest.mark.parametrize("origin", ["MOW", "SVO", "LED"])
@pytest.mark.parametrize("month", range(1, 13))
async def test_every_nonself_destination_has_labelled_roundtrip_flights_every_month_after_the_fixture(
    tmp_path, origin, month
):
    day = date(2028, month, 18)
    party = Travelers(adults=2, children_ages=[0, 5, 5], pets=2)
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as http:
        tool = DemoFlightTool(http=http)
        assert tool.supported_destinations == DESTINATIONS
        for destination in sorted(DESTINATIONS):
            outbound = FlightSearch(origin=origin, destination=destination, departure_date=day, travelers=party)
            first = await tool.run(outbound)
            if origin == destination:
                assert first == []
                continue
            assert first and first == await tool.run(outbound)
            back = await tool.run(
                FlightSearch(
                    origin=destination, destination=origin, departure_date=day + timedelta(days=7), travelers=party
                )
            )
            assert back
            for offers, expected_day, start, end in (
                (first, day, "SVO" if origin == "MOW" else origin, destination),
                (back, day + timedelta(days=7), destination, "SVO" if origin == "MOW" else origin),
            ):
                for item in offers:
                    assert (item.origin.iata, item.destination.iata, item.departure_at.date()) == (
                        start,
                        end,
                        expected_day,
                    )
                    assert item.arrival_at.timestamp() > item.departure_at.timestamp()
                    assert item.is_synthetic and item.price_source is None
                    assert item.demo_source.kind == "deterministic_mock" and "прямота" in item.demo_source.notice
                    assert item.baggage_price_rub == 8000  # Four seats; infant has no independent allowance.
                    assert item.price_rub > 0
        assert not list(tmp_path.iterdir())


async def test_extended_demo_does_not_change_original_synthetic_provider_or_direct_restrictions(tmp_path):
    request = FlightSearch(origin="MOW", destination="CMB", departure_date="2028-02-18")
    synthetic = FlightTool()
    assert synthetic.supported_destinations == frozenset({"AYT", "DXB", "MLE"})
    assert await synthetic.run(request) == []
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as http:
        with pytest.raises(CacheMiss):
            await FlightTool(mode="provider", http=http).run(request)
        demo = DemoFlightTool(http=http)
        assert await demo.run(request.model_copy(update={"direct_only": True}))
        assert await demo.run(request.model_copy(update={"destination": "IAD"})) == []
        assert await demo.run(request.model_copy(update={"origin": "LED", "destination": "LED"})) == []
        old_connected = request.model_copy(update={"origin": "LED", "destination": "MLE", "direct_only": True})
        assert await demo.run(old_connected) == []


async def test_new_route_group_infant_pet_baggage_and_calendar_prices_share_the_same_model(tmp_path):
    party = Travelers(adults=2, children_ages=[0, 5, 5], pets=2)
    query = FlightSearch(origin="MOW", destination="CMB", departure_date="2028-02-18")
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as http:
        tool = DemoFlightTool(http=http)
        solo = await tool.run(query)
        group = await tool.run(query.model_copy(update={"travelers": party}))
        solo_by_time = {row.departure_at: row for row in solo}
        for flight in group:
            assert flight.price_rub == group_fare(solo_by_time[flight.departure_at].price_rub, party)
            assert flight.baggage_price_rub == 8000
        calendar = await price_calendar(
            PriceCalendarQuery(origin="MOW", destination="CMB", month="2028-02", travelers=party), tool.run
        )
        assert len(calendar.days) == 29
        for item in calendar.days:
            offers = await tool.run(query.model_copy(update={"departure_date": item.date, "travelers": party}))
            assert item.min_price_rub == min(flight.price_rub for flight in offers)
            assert item.is_synthetic is True


@pytest.mark.parametrize("local_hour", [2, 12])
async def test_every_fictional_hotel_has_safe_family_pet_minivan_and_explicit_shuttle_limits(local_hour):
    tool = S4TransferTool(include_demo=True)
    assert tool.supported_destinations == DESTINATIONS
    party = Travelers(adults=3, children_ages=[0, 2, 5, 5, 12], pets=3)
    for hotel in HOTELS:
        pickup = datetime(2028, 2, 18, local_hour, tzinfo=ZoneInfo(hotel["timezone"]))
        quotes = await tool.run(
            TransferRequest(airport=hotel["destination"], hotel_id=hotel["hotel_id"], pickup_at=pickup, travelers=party)
        )
        assert {quote.mode for quote in quotes} == {"taxi", "minivan", "shuttle"}
        by_mode = {quote.mode: quote for quote in quotes}
        van = by_mode["minivan"]
        assert van.available and van.hours is None and van.depart_at == pickup
        assert van.pets is True and van.child_seat is True and van.prebook_hours == 0
        assert van.vehicles == math.ceil(party.count / 7)
        assert van.issues == ["extra_vehicle"]
        assert van.destination.id == hotel["hotel_id"] and van.destination.latitude == hotel["latitude"]
        assert all(quote.is_synthetic and quote.price_rub > 0 for quote in quotes)
        assert by_mode["taxi"].pets is None and "pets_unknown" in by_mode["taxi"].issues
        shuttle = by_mode["shuttle"]
        assert shuttle.pets is False and "pets_forbidden" in shuttle.issues
        assert shuttle.available is (local_hour == 12)
        assert ("unavailable_at_pickup" in shuttle.issues) is (local_hour == 2)
        leg = transfer_leg(van)
        assert leg.arrive_at.timestamp() - leg.depart_at.timestamp() == van.duration_minutes * 60


async def test_shuttle_infants_are_free_and_seated_children_have_an_explicit_child_fare():
    hotel = next(hotel for hotel in HOTELS if hotel["destination"] == "CMB")
    record = TransferBase.load(DATA / "demo/transfers.generated.json").get(hotel["hotel_id"], "CMB")
    tariff = next(option.pricing for option in record.options if option.mode == "shuttle")
    request = TransferRequest(
        airport="CMB",
        hotel_id=hotel["hotel_id"],
        pickup_at="2028-02-18T06:30:00Z",
        travelers=Travelers(adults=2, children_ages=[0, 1, 2, 11, 12]),
    )
    quotes = await S4TransferTool(include_demo=True).run(request)
    shuttle = next(quote for quote in quotes if quote.mode == "shuttle")
    assert shuttle.price_rub == 3 * tariff.price_rub + 2 * tariff.child_price_rub
    assert shuttle.vehicles is None


async def test_generated_transfers_require_explicit_opt_in_and_custom_roots_never_use_repo_fallback(tmp_path):
    hotel = next(hotel for hotel in HOTELS if hotel["destination"] == "CMB")
    request = TransferRequest(airport="CMB", hotel_id=hotel["hotel_id"], pickup_at="2028-02-18T12:00:00Z")
    assert S4TransferTool().supported_destinations == frozenset({"AYT", "DXB", "MLE"})
    with pytest.raises(UnsupportedTransfer):
        await S4TransferTool().run(request)
    custom = tmp_path / "data"
    for folder in ("routes", "transfers", "hotels"):
        shutil.copytree(DATA / folder, custom / folder)
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path / "cache") as http:
        with pytest.raises(FileNotFoundError):
            await DemoFlightTool(http=http, data_dir=custom).run(
                FlightSearch(origin="MOW", destination="CMB", departure_date="2028-02-18")
            )
    with pytest.raises(FileNotFoundError):
        await S4TransferTool(include_demo=True, data_dir=custom).run(request)
    assert len(list(TransferBase.load(DATA / "transfers/transfers.json"))) == 60
