from datetime import date

import pytest

from app.config import Settings
from app.contracts.tools import FlightSearch
from app.tools.data_services import open_data_services
from app.tools.http_cache import CacheMiss


async def test_explicit_provider_mode_never_silently_falls_back_to_demo(tmp_path):
    settings = Settings(_env_file=None, cache_mode="replay", data_cache_dir=tmp_path)
    async with open_data_services(settings, transport_mode="provider") as services:
        with pytest.raises(CacheMiss):
            await services.flights.run(FlightSearch(origin="SVO", destination="DXB", departure_date=date(2026, 11, 15)))


async def test_synthetic_mode_uses_configured_seed_without_provider_calls(tmp_path):
    settings = Settings(_env_file=None, cache_mode="replay", data_cache_dir=tmp_path, data_seed=123)
    async with open_data_services(settings) as services:
        offers = await services.flights.run(
            FlightSearch(origin="SVO", destination="DXB", departure_date=date(2026, 11, 15))
        )
    assert offers and all(item.is_synthetic and item.id.endswith("s123") for item in offers)
    assert list(tmp_path.iterdir()) == []
