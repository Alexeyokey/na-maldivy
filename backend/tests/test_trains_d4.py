"""Optional integration with independent D4; no hidden dependency for D6."""

import importlib.util
from datetime import date

import httpx
import pytest
from test_trains import cache_contents, train_query, yandex_fixture

from app.tools.trains import TrainTool, train_price
from app.tools.transport import ProviderNotConfigured

if importlib.util.find_spec("app.tools.http_cache") is None:
    pytest.importorskip("app.tools.http_cache", reason="D4 is in independent PR #56")
from app.tools.http_cache import CachedHttpClient, CacheMiss


async def test_actual_provider_shape_night_normalization_record_and_replay(tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=yandex_fixture())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as source:
        http = CachedHttpClient(mode="record", cache_dir=tmp_path, client=source)
        result = await TrainTool(mode="provider", http=http, key="TEST-NOT-A-REAL-KEY").search_with_provenance(
            train_query()
        )
    request = requests[0]
    assert request.url.host == "api.rasp.yandex-net.ru"
    assert request.url.params["from"] == "c146" and request.url.params["to"] == "c213"
    assert request.url.params["transport_types"] == "train"
    assert request.headers["Authorization"] == "TEST-NOT-A-REAL-KEY"
    assert "apikey" not in str(request.url)
    train = result.trains[0]
    assert train.number == "DEMO002" and train.is_synthetic
    assert train.arrival_at.date() == date(2030, 11, 19)
    assert train.price_rub == train_price(train_query())
    assert result.provenance[0]["schedule"] == "provider timetable"
    assert "unknown" in result.provenance[0]["class_availability"]
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as replay:
        assert await TrainTool(mode="provider", http=replay).search_with_provenance(train_query()) == result
    assert all("TEST-NOT-A-REAL-KEY" not in text for text in cache_contents(tmp_path))


async def test_unconfigured_and_empty_replay_do_not_produce_fake_live_trains(tmp_path):
    async with CachedHttpClient(mode="live", cache_dir=tmp_path) as http:
        with pytest.raises(ProviderNotConfigured):
            await TrainTool(mode="provider", http=http).run(train_query())
    async with CachedHttpClient(mode="replay", cache_dir=tmp_path) as http:
        with pytest.raises(CacheMiss):
            await TrainTool(mode="provider", http=http).run(train_query())
    with pytest.raises(ProviderNotConfigured):
        await TrainTool(mode="provider").run(train_query())
