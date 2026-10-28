"""Chat-level provider/D4 checks use fabricated quotes and never real credentials."""

import base64
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.main import create_app
from app.tools.http_cache import CachedHttpClient, CacheMiss
from app.tools.registry import create_tools, run_tool

CANARY = "FAKE-AVIASALES-CANARY-NOT-A-CREDENTIAL"
MESSAGE = "Из Москвы в Анталью 18 ноября 2027 на неделю вдвоём"
QUERY = {"origin": "SVO", "destination": "AYT", "departure_date": "2027-11-18", "travelers": {"adults": 2}}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    # _env_file=None disables files; remove inherited provider secrets without inspecting them.
    for name in ("AVIASALES_API_TOKEN", "FLIGHTS_MODE", "TOOLS_MODE", "CACHE_MODE", "AVIASALES_CACHE_DIR"):
        monkeypatch.delenv(name, raising=False)


def settings(cache_dir: Path, *, mode="record", token=CANARY):
    return Settings(
        _env_file=None,
        llm_provider="fake",
        tools_mode="real",
        flights_mode="aviasales",
        cache_mode=mode,
        aviasales_api_token=token,
        aviasales_cache_dir=cache_dir,
    )


def parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((fields.get("event", "message"), json.loads(fields.get("data", "null"))))
    return events


def chat(config):
    with TestClient(create_app(config)) as client:
        response = client.post("/api/chat", json={"message": MESSAGE})
        assert response.status_code == 200
        events = parse_sse(response.text)
        assert events[-1][0] == "done"
    return response.text, events


def quote_response(request, *, status=200):
    query = request.url.params
    outbound = query["origin"] == "SVO"
    expected = ("SVO", "AYT", "2027-11-18") if outbound else ("AYT", "SVO", "2027-11-25")
    assert (query["origin"], query["destination"], query["departure_at"]) == expected
    assert query["one_way"] == "true"
    return httpx.Response(
        status,
        request=request,
        json={
            "success": status == 200,
            "currency": "rub",
            "diagnostic": f"Provider echoed {CANARY}",
            "data": [
                {
                    "origin_airport": expected[0],
                    "destination_airport": expected[1],
                    "departure_at": expected[2] + ("T06:00:00+03:00" if outbound else "T19:00:00+03:00"),
                    "price": 14321 if outbound else 16789,
                    "airline": "SU",
                    "flight_number": "2142" if outbound else "2143",
                    "transfers": 0,
                    "duration_to": 180,
                    "link": "/search/fixture",
                }
            ],
        },
    )


def package_options(events):
    return next(data["items"] for name, data in events if name == "options" and data["kind"] == "packages")


def assert_quoted_package(row, *, mode, hit):
    for leg, seat in (("outbound", 14321), ("inbound", 16789)):
        flight = row[leg]
        assert flight["price_rub"] == 2 * seat
        assert flight["baggage_price_rub"] == 4000
        source = flight["price_source"]
        assert source["provider"] == "aviasales_data_api"
        assert source["seat_price_rub"] == seat
        assert source["currency"] == "RUB"
        assert source["cache_mode"] == mode and source["cache_hit"] is hit
        assert source["duration_is_modelled"] is False
        assert source["group_price_is_modelled"] is True
        assert source["baggage_price_is_modelled"] is True
        if mode == "replay":
            assert source["recorded_at"]
    expected = 2 * (14321 + 16789) + 8000 + row["hotel"]["price_rub"]
    expected += row["transfer"]["price_rub"] if row["transfer"] else 0
    assert row["known_total_rub"] == expected


def test_chat_uses_provider_seat_quotes_and_replays_d4_without_token_or_network(tmp_path, monkeypatch, caplog):
    calls = []

    async def send(client, request, **kwargs):
        assert request.url.host == "api.travelpayouts.com"
        assert request.headers["X-Access-Token"] == CANARY
        calls.append(request)
        return quote_response(request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    text, events = chat(settings(tmp_path))
    assert len(calls) == 2
    options = package_options(events)
    assert options
    for option in options:
        assert_quoted_package(option, mode="record", hit=False)
    answer = "".join(data["text"] for name, data in events if name == "delta")
    assert "Aviasales" in answer and "14 321 ₽" in answer and "16 789 ₽" in answer
    assert "Цена группы, багаж, отель и трансфер — расчётные" in answer
    assert "Наличие мест и окончательная стоимость покупки не проверены" in answer
    tool_results = [data for name, data in events if name == "tool_result" and data["name"] == "search_flights"]
    assert len(tool_results) == 2
    assert all(data["ok"] and data["source"] == "aviasales_data_api" for data in tool_results)
    assert all(data["cache_mode"] == "record" for data in tool_results)
    fixtures = list(tmp_path.rglob("*.json"))
    assert len(fixtures) == 2
    for fixture in fixtures:
        raw = fixture.read_text(encoding="utf-8")
        body = base64.b64decode(json.loads(raw)["response"]["body_base64"]).decode("utf-8")
        assert CANARY not in raw and CANARY not in body
        assert "[REDACTED]" in body
    assert CANARY not in text and CANARY not in caplog.text

    async def forbidden_network(*args, **kwargs):
        pytest.fail("D4 replay must never open the network")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_network)
    replay_text, replay_events = chat(settings(tmp_path, mode="replay", token=None))
    replay_options = package_options(replay_events)
    assert len(replay_options) == len(options)
    for replayed, recorded in zip(replay_options, options, strict=True):
        assert_quoted_package(replayed, mode="replay", hit=True)
        assert replayed["known_total_rub"] == recorded["known_total_rub"]
        assert replayed["outbound"]["id"] == recorded["outbound"]["id"]
    assert CANARY not in replay_text and CANARY not in caplog.text


@pytest.mark.parametrize("failure", ["unauthorized", "cache_miss", "transport_error"])
def test_provider_failure_never_substitutes_synthetic_fares(tmp_path, monkeypatch, caplog, failure):
    async def send(client, request, **kwargs):
        if failure == "cache_miss":
            pytest.fail("An empty replay cache must remain offline")
        if failure == "transport_error":
            raise httpx.ConnectError(f"A provider error echoed {CANARY}", request=request)
        return quote_response(request, status=401)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    config = settings(
        tmp_path,
        mode="replay" if failure == "cache_miss" else "record",
        token=None if failure == "cache_miss" else CANARY,
    )
    text, events = chat(config)
    assert not package_options(events)
    assert any(
        name == "tool_result" and data["name"] == "search_flights" and data["ok"] is False for name, data in events
    )
    assert CANARY not in text and CANARY not in caplog.text


def test_app_shutdown_closes_its_owned_d4_client(tmp_path, monkeypatch):
    closed = []
    original_close = CachedHttpClient.aclose

    async def close(cache):
        closed.append(cache)
        await original_close(cache)

    monkeypatch.setattr(CachedHttpClient, "aclose", close)
    with TestClient(create_app(settings(tmp_path, mode="replay", token=None))) as client:
        assert client.get("/health").status_code == 200
        assert not closed
    assert len(closed) == 1


@pytest.mark.parametrize("mode", ["live", "record"])
def test_online_provider_requires_key_without_leaking_input(tmp_path, mode):
    with pytest.raises(ValidationError):
        settings(tmp_path, mode=mode, token=None)


def test_configuration_repr_and_validation_error_do_not_reveal_key(tmp_path):
    config = settings(tmp_path)
    assert isinstance(config.aviasales_api_token, SecretStr)
    assert CANARY not in str(config) and CANARY not in repr(config)
    with pytest.raises(ValidationError) as error:
        Settings(
            _env_file=None,
            tools_mode="stub",
            flights_mode="aviasales",
            cache_mode="live",
            aviasales_api_token=CANARY,
        )
    assert CANARY not in str(error.value) and CANARY not in repr(error.value)


async def test_registry_replay_miss_is_an_error_for_direct_tool_call(tmp_path, monkeypatch):
    async def forbidden_network(*args, **kwargs):
        pytest.fail("D4 replay miss must not access network")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_network)
    tools = create_tools(settings(tmp_path, mode="replay", token=None))
    try:
        with pytest.raises(CacheMiss):
            await run_tool("search_flights", QUERY, tools=tools)
    finally:
        await tools.aclose()


async def test_calendar_uses_the_same_provider_and_does_not_invent_prices_for_empty_days(tmp_path, monkeypatch):
    calls = []

    async def send(client, request, **kwargs):
        calls.append(request.url.params["departure_at"])
        if calls[-1] == "2027-11-18":
            return quote_response(request)
        return httpx.Response(200, request=request, json={"success": True, "currency": "rub", "data": []})

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    tools = create_tools(settings(tmp_path))
    query = {"origin": "SVO", "destination": "AYT", "month": "2027-11", "travelers": {"adults": 2}}
    try:
        days = await run_tool("price_calendar", query, tools=tools)
    finally:
        await tools.aclose()
    assert len(calls) == 30 and len(set(calls)) == 30
    assert [(day.date.isoformat(), day.price_rub) for day in days] == [("2027-11-18", 28642)]


async def test_registry_does_not_close_an_injected_caller_owned_d4(tmp_path):
    http = CachedHttpClient(mode="replay", cache_dir=tmp_path)
    tools = create_tools(settings(tmp_path, mode="replay", token=None), flight_http=http)
    try:
        await tools.aclose()
        # A usable empty replay client reports CacheMiss; a prematurely closed one reports CacheError.
        with pytest.raises(CacheMiss):
            await http.get("https://api.travelpayouts.com/aviasales/v3/prices_for_dates")
    finally:
        await http.aclose()
