from datetime import date

import pytest

from app.config import Settings
from app.contracts.tools import FlightSearch, TransferRequest, Travelers
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


async def test_factory_exposes_s4_with_shared_party_and_no_network(tmp_path):
    settings = Settings(_env_file=None, cache_mode="replay", data_cache_dir=tmp_path)
    async with open_data_services(settings) as services:
        party = Travelers(adults=2, children_ages=[5], pets=1)
        results = await services.transfers.run(
            TransferRequest(
                airport="MLE",
                hotel_id="osm:way/133845532",
                pickup_at="2026-11-18T18:40:00Z",
                travelers=party,
            )
        )
        assert results
        assert services.transfer_base.get("osm:way/133845532", "MLE")
    assert all(result.is_synthetic and result.pickup_at.hour == 23 for result in results)
    assert list(tmp_path.iterdir()) == []
