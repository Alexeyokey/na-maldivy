import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.contracts.tools import FlightSearch, Place, Travelers
from app.tools.flights import AVIASALES_URL, AviasalesDataClient, FlightTool, generate_flights
from app.tools.http_cache import CachedHttpClient, CacheMiss
from app.tools.transport import DATA_DIR, ProviderNotConfigured, ProviderResponseError, arrival_after


def flight_query(**kwargs):
    return FlightSearch(origin="SVO", destination="DXB", departure_date=date(2030, 11, 18), **kwargs)


def aviasales_fixture():
    return json.loads((DATA_DIR / "routes" / "aviasales_prices.synthetic.json").read_text())["response"]


def cache_contents(path):
    return [item.read_text() for item in path.rglob("*.json")]


def test_seed_stable_and_changes_prices_without_global_random():
    first = generate_flights(flight_query(), seed=42)
    assert first == generate_flights(flight_query(), seed=42)
    assert [f.price_rub for f in first] != [f.price_rub for f in generate_flights(flight_query(), seed=43)]
    assert all(f.is_synthetic and "synthetic" in f.carrier for f in first)
    first[0].price_rub = 0
    assert generate_flights(flight_query())[0].price_rub > 0


@pytest.mark.parametrize("origin", ["SVO", "LED", "MOW"])
@pytest.mark.parametrize("destination", ["AYT", "DXB", "MLE"])
def test_network_and_reverse_are_covered(origin, destination):
    forward = FlightSearch(origin=origin, destination=destination, departure_date=date(2030, 11, 18))
    reverse = FlightSearch(origin=destination, destination=origin, departure_date=date(2030, 11, 25))
    assert len(generate_flights(forward)) == len(generate_flights(reverse)) == 2
    assert all(f.destination.iata == ("SVO" if origin == "MOW" else origin) for f in generate_flights(reverse))


def test_no_unverified_direct_led_male_or_unknown_route():
    connecting = FlightSearch(origin="LED", destination="MLE", departure_date=date(2030, 11, 18))
    assert all(f.stops == 1 for f in generate_flights(connecting))
    assert generate_flights(connecting.model_copy(update={"direct_only": True})) == []
    assert generate_flights(flight_query().model_copy(update={"destination": "JFK"})) == []


def test_group_baggage_and_max_price_are_not_multiplied_twice():
    single = generate_flights(flight_query())[0]
    party = Travelers(adults=2, children_ages=[5], pets=1)
    family = generate_flights(flight_query(travelers=party))[0]
    assert family.price_rub == single.price_rub * 3 + 1500
    assert family.baggage_price_rub == 6000
    assert generate_flights(flight_query(max_price_rub=single.price_rub - 1)) == []
    assert generate_flights(flight_query(max_price_rub=single.price_rub))[0].price_rub == single.price_rub


def test_overnight_year_change_and_elapsed_utc_duration():
    query = flight_query().model_copy(update={"departure_date": date(2030, 12, 31)})
    night = next(f for f in generate_flights(query) if f.departure_at.hour == 23)
    assert night.arrival_at.date() == date(2031, 1, 1)
    assert night.is_overnight
    assert night.departure_at.utcoffset() == timedelta(hours=3)
    assert night.arrival_at.utcoffset() == timedelta(hours=4)
    elapsed = night.arrival_at.astimezone(UTC) - night.departure_at.astimezone(UTC)
    assert timedelta(minutes=390) <= elapsed <= timedelta(minutes=405)
    assert len(generate_flights(query.model_copy(update={"departure_date": date.max}))) == 1


def test_elapsed_time_does_not_add_wall_clock_hours_through_dst_gap():
    berlin = Place(kind="airport", iata="BER", name="Berlin", latitude=52.4, longitude=13.5, timezone="Europe/Berlin")
    departure = datetime(2030, 3, 31, 1, 30, tzinfo=ZoneInfo("Europe/Berlin"))
    arrival = arrival_after(departure, 120, berlin)
    assert (arrival.hour, arrival.minute) == (4, 30)
    assert arrival.utcoffset() == timedelta(hours=2)


async def test_provider_record_replay_without_key_and_mixed_provenance(tmp_path: Path):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=aviasales_fixture())

    party = Travelers(adults=2, children_ages=[5])
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as source:
        http = CachedHttpClient(mode="record", cache_dir=tmp_path, client=source)
        tool = FlightTool(mode="provider", http=http, token="TEST-NOT-A-REAL-TOKEN")
        result = await tool.search_with_provenance(flight_query(travelers=party))
    assert len(requests) == 1
    assert requests[0].url.path == "/aviasales/v3/prices_for_dates"
    assert requests[0].url.params["one_way"] == "true"
    assert requests[0].headers["X-Access-Token"] == "TEST-NOT-A-REAL-TOKEN"
    assert "token" not in str(requests[0].url)
    assert result.flights[0].is_synthetic
    assert result.flights[0].price_rub == 60000
    assert result.flights[0].departure_at.hour == 23
    assert result.flights[0].arrival_at.date() == date(2030, 11, 19)
    assert result.flights[0].arrival_at.hour == 7
    assert result.quotes[0].price == 20000
    assert result.provenance[0]["quote"]["price"] == 20000
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as replay:
        reproduced = await FlightTool(mode="provider", http=replay).search_with_provenance(
            flight_query(travelers=party)
        )
    assert result == reproduced
    assert all("TEST-NOT-A-REAL-TOKEN" not in text for text in cache_contents(tmp_path))


async def test_unconfigured_miss_and_provider_error_never_fallback(tmp_path):
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as replay:
        with pytest.raises(CacheMiss):
            await FlightTool(mode="provider", http=replay).run(flight_query())
    async with CachedHttpClient(mode="live", cache_dir=tmp_path) as live:
        with pytest.raises(ProviderNotConfigured):
            await AviasalesDataClient(live).prices_for_dates(flight_query())
    with pytest.raises(ProviderNotConfigured):
        await FlightTool(mode="provider").run(flight_query())
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as source:
        http = CachedHttpClient(mode="live", cache_dir=tmp_path, client=source)
        with pytest.raises(httpx.HTTPStatusError):
            await FlightTool(mode="provider", http=http, token="TEST").run(flight_query())


@pytest.mark.parametrize("change", ["roundtrip", "failed", "naive", "currency"])
async def test_reject_invalid_or_wrong_kind_of_quotes(change, tmp_path):
    payload = aviasales_fixture()
    if change == "roundtrip":
        payload["data"][0]["return_at"] = "2030-11-25T12:00:00Z"
    elif change == "failed":
        payload["success"] = False
    elif change == "naive":
        payload["data"][0]["departure_at"] = "2030-11-18T23:30:00"
    else:
        payload["currency"] = "usd"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as source:
        http = CachedHttpClient(mode="live", cache_dir=tmp_path, client=source)
        with pytest.raises(ProviderResponseError):
            await FlightTool(mode="provider", http=http, token="TEST").run(flight_query())


def test_fixture_and_route_facts_are_separately_labelled():
    fixture = json.loads((DATA_DIR / "routes" / "aviasales_prices.synthetic.json").read_text())
    network = json.loads((DATA_DIR / "routes" / "network.json").read_text())
    assert fixture["fixture_metadata"]["is_captured_provider_response"] is False
    assert len(network["routes"]) == 6
    assert all(route["sources"] for route in network["routes"])
    assert AVIASALES_URL.startswith("https://api.travelpayouts.com/")


async def test_mutating_returned_provenance_cannot_corrupt_cached_route_sources():
    tool = FlightTool()
    first = await tool.search_with_provenance(flight_query())
    original_sources = list(first.provenance[0]["route_sources"])
    first.provenance[0]["route_sources"].clear()
    second = await tool.search_with_provenance(flight_query())
    assert second.provenance[0]["route_sources"] == original_sources
    assert first.provenance[1]["route_sources"] == original_sources
