import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.contracts.tools import FlightSearch, Place, Travelers
from app.tools.flights import AVIASALES_URL, AviasalesDataClient, FlightTool, generate_flights
from app.tools.registry import run_tool
from app.tools.transport import DATA_DIR, ProviderNotConfigured, ProviderResponseError, arrival_after


def flight_query(**kwargs):
    return FlightSearch(origin="SVO", destination="DXB", departure_date=date(2030, 11, 18), **kwargs)


def aviasales_fixture():
    return json.loads((DATA_DIR / "routes" / "aviasales_prices.synthetic.json").read_text())["response"]


class MockFlightHttpClient:
    """D2-only test transport; recording/replay is tested against real D4 separately."""

    mode = "live"

    def __init__(self, client):
        self.client = client

    async def get(self, url, **kwargs):
        return await self.client.get(url, **kwargs)


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


async def test_provider_request_and_mixed_provenance():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=aviasales_fixture())

    party = Travelers(adults=2, children_ages=[5])
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as source:
        http = MockFlightHttpClient(source)
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


async def test_unconfigured_and_provider_error_never_fallback():
    def forbidden_request(_request):
        pytest.fail("Unconfigured provider must fail before sending HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden_request)) as source:
        live = MockFlightHttpClient(source)
        with pytest.raises(ProviderNotConfigured):
            await AviasalesDataClient(live).prices_for_dates(flight_query())
    with pytest.raises(ProviderNotConfigured):
        await FlightTool(mode="provider").run(flight_query())
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as source:
        http = MockFlightHttpClient(source)
        with pytest.raises(httpx.HTTPStatusError):
            await FlightTool(mode="provider", http=http, token="TEST").run(flight_query())


@pytest.mark.parametrize("change", ["roundtrip", "failed", "naive", "currency"])
async def test_reject_invalid_or_wrong_kind_of_quotes(change):
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
        http = MockFlightHttpClient(source)
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


@pytest.mark.parametrize("last_page_size", [0, 1])
async def test_provider_pagination_reads_every_page_and_stops_at_short_page(last_page_size):
    requested_pages = []
    template = aviasales_fixture()["data"][0]

    def respond(request):
        page = int(request.url.params["page"])
        requested_pages.append(page)
        size = 100 if page == 1 else last_page_size
        return httpx.Response(
            200,
            json={
                "success": True,
                "currency": "rub",
                "data": [{**template, "flight_number": f"PAGE-{page}-{index}"} for index in range(size)],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as source:
        result = await AviasalesDataClient(MockFlightHttpClient(source), "TEST").prices_for_dates(flight_query())
    assert requested_pages == [1, 2]
    assert len(result) == 100 + last_page_size
    assert result[99].flight_number == "PAGE-1-99"
    if last_page_size:
        assert result[100].flight_number == "PAGE-2-0"


async def test_provider_max_pages_rejects_truncated_result():
    requested_pages = []
    template = aviasales_fixture()["data"][0]

    def respond(request):
        requested_pages.append(int(request.url.params["page"]))
        return httpx.Response(200, json={"success": True, "data": [template] * 100})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as source:
        provider = AviasalesDataClient(MockFlightHttpClient(source), "TEST", max_pages=2)
        with pytest.raises(ProviderResponseError, match="incomplete"):
            await provider.prices_for_dates(flight_query())
    assert requested_pages == [1, 2]


async def test_provider_uses_airport_local_date_and_filters_other_airports_and_connections():
    template = aviasales_fixture()["data"][0]
    payload = {
        "success": True,
        "data": [
            # UTC date is the previous day, but SVO departure is 18 November.
            {**template, "flight_number": "LOCAL-MATCH", "departure_at": "2030-11-17T22:30:00Z"},
            {**template, "flight_number": "WRONG-DAY", "departure_at": "2030-11-18T22:30:00Z"},
            {**template, "flight_number": "WRONG-ORIGIN", "origin_airport": "DME"},
            {**template, "flight_number": "WRONG-DESTINATION", "destination_airport": "DWC"},
            {**template, "flight_number": "CONNECTION", "transfers": 1},
        ],
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as source:
        provider = AviasalesDataClient(MockFlightHttpClient(source), "TEST")
        direct = await provider.prices_for_dates(flight_query(direct_only=True))
        connecting = await provider.prices_for_dates(flight_query())
    assert [item.flight_number for item in direct] == ["LOCAL-MATCH"]
    assert [item.flight_number for item in connecting] == ["LOCAL-MATCH", "CONNECTION"]


async def test_provider_empty_response_is_empty_not_synthetic_fallback():
    payload = {"success": True, "data": []}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as source:
        result = await FlightTool(
            mode="provider", http=MockFlightHttpClient(source), token="TEST"
        ).search_with_provenance(flight_query())
    assert result.flights == result.quotes == result.provenance == ()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"success": "true", "data": []},
        {"success": True, "data": {}},
        {"success": True, "data": [None]},
        {"success": True, "data": [{}]},
        {"success": True, "currency": None, "data": []},
    ],
)
async def test_provider_rejects_malformed_envelope_or_quote(payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as source:
        with pytest.raises(ProviderResponseError):
            await AviasalesDataClient(MockFlightHttpClient(source), "TEST").prices_for_dates(flight_query())


@pytest.mark.parametrize(
    "changes",
    [
        {"price": -1},
        {"price": "20000"},
        {"price": True},
        {"transfers": -1},
        {"duration_to": 0},
        {"duration_to": "390"},
        {"currency": "USD"},
    ],
)
async def test_provider_rejects_invalid_quote_values(changes):
    payload = aviasales_fixture()
    payload["data"][0].update(changes)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as source:
        with pytest.raises(ProviderResponseError):
            await AviasalesDataClient(MockFlightHttpClient(source), "TEST").prices_for_dates(flight_query())


async def test_provider_modelled_duration_and_group_budget_keep_original_quote():
    payload = aviasales_fixture()
    del payload["data"][0]["duration_to"]
    party = Travelers(adults=2, children_ages=[5], pets=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as source:
        tool = FlightTool(mode="provider", http=MockFlightHttpClient(source), token="TEST")
        exact = await tool.search_with_provenance(flight_query(travelers=party, max_price_rub=61500))
        rejected = await tool.search_with_provenance(flight_query(travelers=party, max_price_rub=61499))
    assert len(exact.flights) == 1
    assert exact.flights[0].price_rub == 61500 and exact.flights[0].baggage_price_rub == 6000
    assert exact.quotes[0].price == 20000
    assert exact.provenance[0]["duration"] == "synthetic estimate"
    assert rejected.flights == rejected.provenance == ()
    assert rejected.quotes == exact.quotes


async def test_flight_tool_runs_through_d1_dispatch_with_group_contract():
    tool = FlightTool(seed=777)
    query = flight_query(travelers=Travelers(adults=2, children_ages=[5], pets=1))
    result = await run_tool("search_flights", query.model_dump(mode="json"), tools={tool.name: tool})
    assert result == generate_flights(query, seed=777)
    assert all(item.origin.kind == item.destination.kind == "airport" for item in result)
    assert all(item.price_rub > 0 and item.is_synthetic for item in result)


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
