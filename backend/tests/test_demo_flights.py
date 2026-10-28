"""Explicit offline demo sources; fabricated D4 records never require credentials."""

import json
from datetime import date
from shutil import copy2

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.contracts.tools import Flight, FlightDemoSource, FlightPriceSource, FlightSearch, Travelers
from app.main import create_app
from app.tools.demo_flights import DemoFlightTool
from app.tools.flights import FlightTool
from app.tools.http_cache import CachedHttpClient, CacheFormatError, CacheMiss
from app.tools.registry import create_tools, run_tool
from app.tools.transport import ProviderResponseError

SEAT_PRICE = 14321
DAY = date(2027, 11, 18)


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    for name in (
        "AVIASALES_API_TOKEN",
        "AVIASALES_CACHE_DIR",
        "FLIGHTS_MODE",
        "TOOLS_MODE",
        "CACHE_MODE",
        "LLM_PROVIDER",
        "YANDEX_RASP_API_KEY",
        "TRAINS_MODE",
    ):
        monkeypatch.delenv(name, raising=False)

    async def forbidden(*_args, **_kwargs):
        pytest.fail("Demo flight tests must not open an external network connection")

    # Real D4 may use a MockTransport to build a record, but never a network transport.
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


def query(*, destination="AYT", departure_date=DAY):
    return FlightSearch(
        origin="SVO", destination=destination, departure_date=departure_date, travelers=Travelers(adults=2)
    )


def payload(search, *, actual_destination=None):
    return {
        "success": True,
        "currency": "rub",
        "data": [
            {
                "origin_airport": search.origin,
                "destination_airport": actual_destination or search.destination,
                "departure_at": search.departure_date.isoformat() + "T06:00:00+03:00",
                "price": SEAT_PRICE,
                "airline": "SU",
                "flight_number": "2142",
                "transfers": 0,
                "duration_to": 180,
                "link": "/search/fabricated-test-quote",
            }
        ],
    }


async def record(cache_dir, search, body, *, status=200):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        async with CachedHttpClient(mode="record", cache_dir=cache_dir, client=client) as http:
            try:
                await FlightTool(mode="provider", http=http, token="FABRICATED-TEST-TOKEN").run(search)
            except ProviderResponseError:
                # The negative response is still a valid D4 record for error propagation checks.
                pass
    assert len(calls) == 1
    assert len(list(cache_dir.glob("*.json"))) == 1


def config(cache_dir, **overrides):
    return Settings(
        _env_file=None,
        **{
            "llm_provider": "fake",
            "tools_mode": "real",
            "flights_mode": "demo",
            "cache_mode": "replay",
            "aviasales_api_token": None,
            "aviasales_cache_dir": cache_dir,
            **overrides,
        },
    )


def sse(response):
    assert response.status_code == 200
    result = []
    for block in response.text.replace("\r\n", "\n").split("\n\n"):
        lines = block.splitlines()
        kind = next((line[7:] for line in lines if line.startswith("event: ")), None)
        body = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if kind and body:
            result.append((kind, json.loads(body)))
    assert result and result[-1][0] == "done"
    return result


async def test_provider_replay_keeps_exact_quote_and_marks_its_demo_source(tmp_path):
    search = query()
    await record(tmp_path, search, payload(search))
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as http:
        expected = await FlightTool(mode="provider", http=http).run(search)
        tool = DemoFlightTool(http=http)
        actual = await tool.run(search)
    assert len(actual) == len(expected) == 1
    assert actual[0].model_dump(exclude={"demo_source"}) == expected[0].model_dump(exclude={"demo_source"})
    source = actual[0].demo_source
    assert source.kind == "provider_replay" and source.reason is None and source.notice
    assert actual[0].price_source.seat_price_rub == SEAT_PRICE
    assert actual[0].price_source.cache_mode == "replay" and actual[0].price_source.cache_hit is True
    assert actual[0].is_synthetic is True
    assert "provider_replay" in json.dumps(tool.public_metadata(actual), ensure_ascii=False)


async def test_cache_miss_uses_reproducible_explicit_mock_without_writing_cache(tmp_path):
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as http:
        tool = DemoFlightTool(http=http, seed=42)
        first = await tool.run(query())
        second = await DemoFlightTool(http=http, seed=42).run(query())
    assert first and first == second
    for flight in first:
        assert flight.id.startswith("demo-mock")
        assert flight.price_source is None and flight.is_synthetic is True
        assert flight.demo_source.kind == "deterministic_mock"
        assert flight.demo_source.reason == "cache_miss" and flight.demo_source.notice
    assert "deterministic_mock" in json.dumps(tool.public_metadata(first), ensure_ascii=False)
    assert not list(tmp_path.iterdir())


async def test_real_quote_above_budget_never_switches_to_a_cheaper_mock(tmp_path, monkeypatch):
    search = query().model_copy(update={"max_price_rub": 1_000})
    body = payload(search)
    body["data"][0]["price"] = 10_000
    await record(tmp_path, search, body)
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as http:
        tool = DemoFlightTool(http=http)

        async def cheap_mock_would_be_wrong(_):
            pytest.fail("A synthetic 500 RUB offer must not bypass an existing expensive real quote")

        monkeypatch.setattr(tool.synthetic, "search_with_provenance", cheap_mock_would_be_wrong)
        result = await tool.search_with_provenance(search)
        assert result.flights == () and len(result.quotes) == 1
        assert result.quotes[0].price == 10_000
        assert await tool.run(search) == []


@pytest.mark.parametrize("empty", ["empty_payload", "wrong_airport"])
async def test_only_successful_empty_or_filtered_quotes_can_fall_back(tmp_path, empty):
    search = query(destination="DXB")
    body = {"success": True, "currency": "rub", "data": []}
    if empty == "wrong_airport":
        body = payload(search, actual_destination="DWC")
    await record(tmp_path, search, body)
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as http:
        result = await DemoFlightTool(http=http).run(search)
    assert result
    assert all(flight.destination.iata == "DXB" for flight in result)
    assert all(flight.price_source is None for flight in result)
    assert all(flight.demo_source.kind == "deterministic_mock" for flight in result)
    assert all(flight.demo_source.reason == "no_matching_quotes" for flight in result)


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (401, {"success": False, "data": []}),
        (503, {"success": False, "data": []}),
        (200, {"success": False, "data": []}),
        (200, {"success": True, "data": "not a quote list"}),
        (200, {"success": True, "data": [{"price": "invalid"}]}),
    ],
)
async def test_http_and_malformed_provider_errors_never_turn_into_mock(tmp_path, status, body):
    search = query()
    await record(tmp_path, search, body, status=status)
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as http:
        with pytest.raises(ProviderResponseError):
            await DemoFlightTool(http=http).run(search)


async def test_corrupt_d4_record_remains_an_error(tmp_path):
    search = query()
    await record(tmp_path, search, payload(search))
    next(tmp_path.glob("*.json")).write_text('{"broken":', encoding="utf-8")
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as http:
        with pytest.raises(CacheFormatError):
            await DemoFlightTool(http=http).run(search)


@pytest.mark.parametrize("mode", ["record", "live"])
def test_demo_tool_rejects_online_http_boundary(tmp_path, mode):
    with pytest.raises(ValueError):
        DemoFlightTool(http=CachedHttpClient(mode=mode, cache_dir=tmp_path))


async def test_demo_tool_still_rejects_a_client_switched_to_live_after_creation(tmp_path):
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as http:
        tool = DemoFlightTool(http=http)
        http.mode = "live"
        with pytest.raises(ValueError, match="offline replay"):
            await tool.run(query())


async def test_mock_source_cannot_carry_aviasales_evidence_or_claim_real_inventory():
    generated = (await FlightTool().run(query()))[0]
    raw = generated.model_dump()
    raw["demo_source"] = FlightDemoSource(
        kind="deterministic_mock", reason="cache_miss", notice="Explicit test mock"
    ).model_dump()
    raw["price_source"] = FlightPriceSource(
        seat_price_rub=10_000, cache_mode="replay", cache_hit=True, duration_is_modelled=True
    ).model_dump()
    with pytest.raises(ValidationError, match="cannot carry an Aviasales"):
        Flight.model_validate(raw)
    raw["price_source"] = None
    raw["is_synthetic"] = False
    with pytest.raises(ValidationError, match="remain modelled"):
        Flight.model_validate(raw)


async def test_demo_registry_ignores_a_configured_token_and_uses_public_cache_by_default(tmp_path):
    tools = create_tools(config(tmp_path, aviasales_api_token="UNUSED-DEMO-CANARY", aviasales_cache_dir=None))
    try:
        tool = tools["search_flights"]
        assert tool.provider.provider.token is None
        assert tool.http.cache_dir == Settings(_env_file=None).data_dir / "demo/aviasales"
        assert tools["price_calendar"].flights is tool
        assert "UNUSED-DEMO-CANARY" not in json.dumps(tool.public_metadata([]))
    finally:
        await tools.aclose()


@pytest.mark.parametrize("overrides", [{"tools_mode": "stub"}, {"cache_mode": "record"}, {"cache_mode": "live"}])
def test_demo_configuration_requires_real_tools_and_replay(tmp_path, overrides):
    with pytest.raises(ValidationError):
        config(tmp_path, **overrides)


async def test_aviasales_mode_stays_strict_and_synthetic_mode_does_not_claim_demo_fallback(tmp_path):
    tools = create_tools(config(tmp_path, flights_mode="aviasales"))
    try:
        with pytest.raises(CacheMiss):
            await run_tool("search_flights", query().model_dump(mode="json"), tools=tools)
    finally:
        await tools.aclose()
    synthetic = create_tools(config(tmp_path, flights_mode="synthetic"))
    try:
        result = await run_tool("search_flights", query().model_dump(mode="json"), tools=synthetic)
        assert result and all(flight.demo_source is None and flight.price_source is None for flight in result)
    finally:
        await synthetic.aclose()


async def test_http_chat_keeps_provider_and_mock_leg_sources_separate(tmp_path):
    search = query()
    await record(tmp_path, search, payload(search))
    settings = config(tmp_path, checkout_db_path=tmp_path / "checkout.sqlite3")
    with TestClient(create_app(settings)) as client:
        client.app.state.agent._today = lambda: date(2026, 10, 28)
        events = sse(client.post("/api/chat", json={"message": "Из Москвы в Анталью 18 ноября 2027 на неделю вдвоём"}))
    options = next(body["items"] for kind, body in events if kind == "options" and body["kind"] == "packages")
    assert options
    for option in options:
        outbound, inbound = option["outbound"], option["inbound"]
        assert outbound["demo_source"]["kind"] == "provider_replay"
        assert outbound["price_source"]["seat_price_rub"] == SEAT_PRICE
        assert outbound["price_rub"] == 2 * SEAT_PRICE
        assert inbound["demo_source"]["kind"] == "deterministic_mock"
        assert inbound["demo_source"]["reason"] == "cache_miss" and inbound["price_source"] is None
    visible = "".join(body["text"] for kind, body in events if kind == "delta")
    assert "Aviasales" in visible
    assert any(label in visible.casefold() for label in ("демонстрацион", "mock", "демо-рейс", "демо-мок"))


async def test_d8_mixed_demo_sources_stay_a_demonstration_estimate_and_preserve_state(tmp_path):
    search = query()
    await record(tmp_path, search, payload(search))
    settings = config(tmp_path, checkout_db_path=tmp_path / "checkout.sqlite3")
    with TestClient(create_app(settings)) as client:
        client.app.state.agent._today = lambda: date(2026, 10, 28)
        events = sse(client.post("/api/chat", json={"message": "Из Москвы в Анталью 18 ноября 2027 на неделю вдвоём"}))
        session_id = next(body["session_id"] for kind, body in events if kind == "done")
        before = client.get(f"/api/session/{session_id}").json()
        compared = sse(
            client.post(
                "/api/chat",
                json={"session_id": session_id, "message": "Если сдвинуть даты на 1–3 дня, можно сэкономить?"},
            )
        )
        assert client.get(f"/api/session/{session_id}").json() == before
    facts = next(body for kind, body in compared if kind == "date_savings")
    assert facts["comparison_status"] == "compared"
    assert facts["demo_mode"] is True and facts["savings_kind"] == "demonstration_estimate"
    baseline_sources = json.dumps(facts["baseline_flight_sources"], ensure_ascii=False)
    assert "provider_replay" in baseline_sources and "deterministic_mock" in baseline_sources
    quoted = [row for row in facts["checked_dates"] if row["status"] == "quoted"]
    assert quoted and all(row["contains_mock"] is True for row in quoted)
    assert all("deterministic_mock" in json.dumps(row["flight_sources"]) for row in quoted)
    for suggestion in facts["suggestions"]:
        assert suggestion["contains_mock"] is True and suggestion["savings_kind"] == "demonstration_estimate"
    visible = "".join(body["text"] for kind, body in compared if kind == "delta")
    assert any(label in visible.casefold() for label in ("демонстрацион", "demo mock", "демо-мок"))


async def test_d8_real_baseline_does_not_present_mock_alternatives_as_real_provider_savings(tmp_path):
    outbound = query()
    await record(tmp_path, outbound, payload(outbound))
    inbound = FlightSearch(
        origin="AYT", destination="SVO", departure_date=date(2027, 11, 25), travelers=Travelers(adults=2)
    )
    other = tmp_path / "other"
    await record(other, inbound, payload(inbound))
    for path in other.glob("*.json"):
        copy2(path, tmp_path / path.name)
    with TestClient(create_app(config(tmp_path, checkout_db_path=tmp_path / "checkout.sqlite3"))) as client:
        client.app.state.agent._today = lambda: date(2026, 10, 28)
        initial = sse(client.post("/api/chat", json={"message": "Из Москвы в Анталью 18 ноября 2027 на неделю вдвоём"}))
        session_id = next(body["session_id"] for kind, body in initial if kind == "done")
        packages = next(body["items"] for kind, body in initial if kind == "options")
        assert packages and all(
            package[direction]["demo_source"]["kind"] == "provider_replay"
            for package in packages
            for direction in ("outbound", "inbound")
        )
        compared = sse(
            client.post(
                "/api/chat",
                json={"session_id": session_id, "message": "Если сдвинуть даты на 1–3 дня, можно сэкономить?"},
            )
        )
    facts = next(body for kind, body in compared if kind == "date_savings")
    assert facts["flight_source"] == "aviasales_data_api" and facts["demo_mode"]
    assert facts["contains_mock"] is True and facts["savings_kind"] == "demonstration_estimate"
    assert all(source["kind"] == "provider_replay" for source in facts["baseline_flight_sources"].values())
    assert any(row.get("contains_mock") is True for row in facts["checked_dates"])
    text = "".join(body["text"] for kind, body in compared if kind == "delta")
    assert "не подтверждённая экономия по настоящим предложениям Aviasales" in text


async def test_mock_cannot_create_an_unsupported_route(tmp_path):
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as http:
        assert await DemoFlightTool(http=http).run(query(destination="IAD")) == []
