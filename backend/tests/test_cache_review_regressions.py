import os

import httpx
import pytest

from app.data_paths import DEFAULT_DATA_DIR
from app.tools.currency import CurrencyFetchError, load_cbr_rates
from app.tools.http_cache import CachedHttpClient, CacheError, CacheMiss, ProviderRequestError


def test_default_cache_is_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert CachedHttpClient().cache_dir == DEFAULT_DATA_DIR / "cache"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert CachedHttpClient().cache_dir == tmp_path / "cache"


async def test_record_and_replay_work_without_unix_fchmod(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "fchmod", raising=False)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"price": 123}))
    ) as source:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=source) as client:
            await client.get("https://public.example/price")
    async with CachedHttpClient(cache_dir=tmp_path) as client:
        assert (await client.get("https://public.example/price")).json() == {"price": 123}
    assert len(list(tmp_path.iterdir())) == 1


async def test_failed_chmod_does_not_replace_existing_record_or_leave_temp_file(tmp_path, monkeypatch):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"price": 123}))
    ) as source:
        async with CachedHttpClient(mode="record", cache_dir=tmp_path, client=source) as client:
            await client.get("https://public.example/price")
            before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}

            def unavailable(*args):
                raise OSError("private-path")

            monkeypatch.setattr(os, "chmod", unavailable)
            with pytest.raises(CacheError, match="Cannot atomically write"):
                await client.get("https://public.example/price")
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("error", [CacheMiss("private-path"), ProviderRequestError("private-key")])
async def test_currency_wraps_cache_and_provider_failures_without_echoing_details(error):
    class FailedClient:
        async def get(self, *args, **kwargs):
            raise error

    with pytest.raises(CurrencyFetchError) as result:
        await load_cbr_rates(FailedClient())
    assert "private" not in str(result.value)
    assert result.value.__suppress_context__
