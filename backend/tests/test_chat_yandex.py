"""Model-free chat uses the Yandex adapter through D4; all HTTP is MockTransport.

The hand-written timetable fixture is not a captured live provider response.
"""

import base64
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.main import create_app
from app.tools.http_cache import CachedHttpClient, CacheError
from app.tools.registry import create_tools, run_tool
from app.tools.trains import YANDEX_URL, train_price
from app.u2_eval import ROOT, parse_sse

CANARY = "FAKE-YANDEX-CANARY-NOT-A-CREDENTIAL"
MESSAGE = "Питер Москва поездом 18 ноября 2030 купе на двоих"


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch):
    for name in (
        "TRAINS_MODE",
        "YANDEX_RASP_API_KEY",
        "YANDEX_CACHE_DIR",
        "FLIGHTS_MODE",
        "AVIASALES_API_TOKEN",
        "TOOLS_MODE",
        "CACHE_MODE",
        "LLM_PROVIDER",
        "LLM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def settings(cache_dir: Path, *, mode="record", key=CANARY, **overrides):
    values = {
        "_env_file": None,
        "tools_mode": "real",
        "flights_mode": "synthetic",
        "trains_mode": "yandex",
        "cache_mode": mode,
        "llm_provider": "fake",
        "yandex_rasp_api_key": key,
        "yandex_cache_dir": cache_dir,
        "data_dir": ROOT / "data",
    }
    return Settings(**(values | overrides))


def fixture():
    payload = json.loads((ROOT / "data/trains/yandex_search.synthetic.json").read_text(encoding="utf-8"))["response"]
    payload["diagnostic"] = f"An upstream provider echoed {CANARY}"
    return payload


def chat(config, monkeypatch, *, cache=None):
    if cache is not None:
        monkeypatch.setattr("app.main.create_tools", lambda config: create_tools(config, train_http=cache))
    with TestClient(create_app(config)) as client:
        response = client.post("/api/chat", json={"message": MESSAGE})
        assert response.status_code == 200
        events = parse_sse(response.text)
        assert events[-1]["type"] == "done"
        session = client.get(f"/api/session/{events[-1]['data']['session_id']}")
        assert session.status_code == 200
    return response.text, events, session.json()


def trains(events):
    return next(
        event["data"]["items"] for event in events if event["type"] == "options" and event["data"]["kind"] == "trains"
    )


def result_metadata(events):
    return next(
        event["data"] for event in events if event["type"] == "tool_result" and event["data"]["name"] == "search_trains"
    )


async def test_http_chat_records_schedule_and_replays_without_key_or_network(tmp_path, monkeypatch, caplog):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.host == "api.rasp.yandex-net.ru"
        assert request.headers["Authorization"] == CANARY
        assert CANARY not in str(request.url) and "apikey" not in request.url.params
        assert request.url.params["from"] == "c146" and request.url.params["to"] == "c213"
        assert request.url.params["date"] == "2030-11-18"
        return httpx.Response(200, json=fixture())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as source:
        cache = CachedHttpClient(mode="record", cache_dir=tmp_path, client=source, secret_values=(CANARY,))
        text, events, state = chat(settings(tmp_path), monkeypatch, cache=cache)
        assert len(requests) == 1
        rows = trains(events)
        assert rows and rows[0]["number"] == "DEMO002"
        assert rows[0]["departure_at"] == "2030-11-18T23:55:00+03:00"
        assert rows[0]["arrival_at"] == "2030-11-19T07:55:00+03:00"
        assert rows[0]["seat_class"] == "coupe" and rows[0]["is_synthetic"] is True
        args = next(event["data"]["arguments"] for event in events if event["type"] == "tool_call")
        from app.contracts.tools import TrainSearch

        assert rows[0]["price_rub"] == train_price(TrainSearch.model_validate(args))
        assert state["brief"]["adults"] == 2
        metadata = result_metadata(events)
        assert metadata["source"] == "yandex_timetable" and metadata["cache_mode"] == "record"
        assert metadata["price_modelled"] and not metadata["availability_confirmed"]
        assert not metadata["class_availability_confirmed"]
        answer = "".join(event["data"]["text"] for event in events if event["type"] == "delta")
        assert "стоимость модельная" in answer and "Наличие мест и бронирование не проверены" in answer
        await cache.aclose()

    recorded = list(tmp_path.glob("*.json"))
    assert len(recorded) == 1
    for path in recorded:
        raw = path.read_text(encoding="utf-8")
        decoded = base64.b64decode(json.loads(raw)["response"]["body_base64"]).decode("utf-8")
        assert CANARY not in raw and CANARY not in decoded
        assert "[REDACTED]" in decoded
    assert CANARY not in text and CANARY not in caplog.text

    def forbidden(request):
        pytest.fail("A token-free D4 replay must not reach its HTTP transport")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as source:
        cache = CachedHttpClient(mode="replay", cache_dir=tmp_path, client=source)
        replay_text, replay_events, _ = chat(settings(tmp_path, mode="replay", key=None), monkeypatch, cache=cache)
        assert trains(replay_events) == rows
        assert result_metadata(replay_events)["cache_mode"] == "replay"
        assert result_metadata(replay_events)["source"] == "yandex_timetable"
        assert CANARY not in replay_text
        await cache.aclose()


@pytest.mark.parametrize("failure", ["unauthorized", "bad_json", "empty", "transport_error", "cache_miss"])
async def test_provider_errors_and_missing_quotes_do_not_fall_back_to_demo(tmp_path, monkeypatch, caplog, failure):
    def respond(request):
        if failure == "cache_miss":
            pytest.fail("An empty replay cache must remain offline")
        if failure == "transport_error":
            raise httpx.ConnectError(f"Network diagnostic echoed {CANARY}", request=request)
        if failure == "bad_json":
            return httpx.Response(200, text="not-json " + CANARY)
        if failure == "empty":
            return httpx.Response(200, json={"segments": [], "pagination": {"total": 0}})
        return httpx.Response(401, json={"error": {"text": CANARY}})

    mode = "replay" if failure == "cache_miss" else "record"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as source:
        cache = CachedHttpClient(mode=mode, cache_dir=tmp_path, client=source, secret_values=(CANARY,))
        text, events, _ = chat(
            settings(tmp_path, mode=mode, key=None if mode == "replay" else CANARY), monkeypatch, cache=cache
        )
        assert trains(events) == []
        metadata = result_metadata(events)
        if failure == "empty":
            assert metadata["ok"] is True and metadata["source"] is None
            assert metadata["schedule_quote_count"] == 0
        else:
            assert metadata["ok"] is False and "source" not in metadata
        assert CANARY not in text and CANARY not in caplog.text
        await cache.aclose()


@pytest.mark.parametrize("mode", ["record", "live"])
def test_provider_configuration_requires_key_for_online_mode(tmp_path, mode):
    with pytest.raises(ValidationError, match="YANDEX_RASP_API_KEY"):
        settings(tmp_path, mode=mode, key=None)
    assert settings(tmp_path, mode="replay", key=None).yandex_rasp_api_key is None


def test_secret_repr_and_config_errors_hide_key_and_provider_requires_real_tools(tmp_path):
    config = settings(tmp_path)
    assert isinstance(config.yandex_rasp_api_key, SecretStr)
    assert CANARY not in str(config) and CANARY not in repr(config)
    with pytest.raises(ValidationError) as error:
        settings(tmp_path, tools_mode="stub")
    assert CANARY not in str(error.value)


def test_app_closes_both_factory_owned_provider_caches(tmp_path, monkeypatch):
    closed = []
    original_close = CachedHttpClient.aclose

    async def close(cache):
        closed.append(cache)
        await original_close(cache)

    monkeypatch.setattr(CachedHttpClient, "aclose", close)
    config = settings(
        tmp_path,
        mode="replay",
        key=None,
        flights_mode="aviasales",
        aviasales_api_token=None,
        aviasales_cache_dir=tmp_path / "flights",
    )
    with TestClient(create_app(config)) as client:
        assert client.get("/health").status_code == 200
        assert not closed
    assert len(closed) == 2 and all(cache._closed for cache in closed)


async def test_injected_train_cache_remains_caller_owned(tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=fixture()))
    ) as source:
        cache = CachedHttpClient(mode="record", cache_dir=tmp_path, client=source, secret_values=(CANARY,))
        registry = create_tools(settings(tmp_path), train_http=cache)
        await registry.aclose()
        assert not cache._closed
        rows = await run_tool(
            "search_trains", {"origin": "LED", "destination": "MOW", "departure_date": "2030-11-18"}, tools=registry
        )
        assert rows and rows[0].number == "DEMO002"
        await cache.aclose()
        with pytest.raises(CacheError, match="closed"):
            await cache.get(YANDEX_URL)
