"""Optional integration with real D4, absent until independent PR #56 is merged.

Only absence of app.tools.http_cache skips this module. A broken D4 import or
any failed integration assertion remains a failure.
"""

import importlib
import importlib.util
import json
from datetime import date

import httpx
import pytest

from app.contracts.tools import FlightSearch, Travelers
from app.tools.flights import FlightTool
from app.tools.transport import DATA_DIR

if importlib.util.find_spec("app.tools.http_cache") is None:
    pytest.importorskip("app.tools.http_cache", reason="D4 HTTP cache is in independent PR #56")
cache = importlib.import_module("app.tools.http_cache")


def query():
    return FlightSearch(
        origin="SVO",
        destination="DXB",
        departure_date=date(2030, 11, 18),
        travelers=Travelers(adults=2, children_ages=[5]),
    )


async def test_real_d4_record_replay_without_key_preserves_mixed_provenance(tmp_path, monkeypatch):
    requests = []
    payload = json.loads((DATA_DIR / "routes" / "aviasales_prices.synthetic.json").read_text())["response"]

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as source:
        async with cache.CachedHttpClient(mode="record", cache_dir=tmp_path, client=source) as http:
            result = await FlightTool(mode="provider", http=http, token="TEST-NOT-A-REAL-TOKEN").search_with_provenance(
                query()
            )
    assert len(requests) == 1
    assert result.flights[0].price_rub == 60000
    assert result.quotes[0].price == 20000
    assert result.provenance[0]["quote"]["price"] == 20000
    assert all("TEST-NOT-A-REAL-TOKEN" not in path.read_text() for path in tmp_path.rglob("*.json"))

    async def forbidden_network(*_args, **_kwargs):
        pytest.fail("Replay must not send HTTP")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_network)
    async with cache.CachedHttpClient(mode="replay", cache_dir=tmp_path) as replay:
        reproduced = await FlightTool(mode="provider", http=replay).search_with_provenance(query())
    assert [q.model_dump(mode="json") for q in result.quotes] == [q.model_dump(mode="json") for q in reproduced.quotes]
    assert result.provenance == reproduced.provenance
    for recorded, restored in zip(result.flights, reproduced.flights, strict=True):
        assert recorded.model_dump(exclude={"price_source"}) == restored.model_dump(exclude={"price_source"})
        assert recorded.price_source.cache_mode == "record" and recorded.price_source.cache_hit is False
        assert restored.price_source.cache_mode == "replay" and restored.price_source.cache_hit is True
        assert recorded.price_source.recorded_at == restored.price_source.recorded_at
        assert recorded.price_source.seat_price_rub == restored.price_source.seat_price_rub


async def test_real_d4_replay_miss_never_falls_back_to_network_or_demo(tmp_path, monkeypatch):
    async def forbidden_network(*_args, **_kwargs):
        pytest.fail("A replay miss must not send HTTP")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_network)
    async with cache.CachedHttpClient(mode="replay", cache_dir=tmp_path) as replay:
        with pytest.raises(cache.CacheMiss):
            await FlightTool(mode="provider", http=replay).run(query())
    assert not list(tmp_path.iterdir())
