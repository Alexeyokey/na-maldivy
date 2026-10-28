import json
import shutil

import pytest

from app.contracts.tools import Destination, DestinationQuery
from app.data_paths import DEFAULT_DATA_DIR
from app.tools.d5_adapter import DestinationTool
from app.tools.destinations import Catalogue, load_catalogue
from app.tools.registry import run_tool


async def test_d1_dispatch_preserves_unknown_prices_and_inland_water():
    tool = DestinationTool()
    result = await run_tool(tool.name, {"month": 11, "origin": "SVO"}, tools={tool.name: tool})
    assert len(result) == 48
    assert all(Destination.model_validate_json(item.model_dump_json()) == item for item in result)
    assert all(item.estimated_total_price_rub is None for item in result)
    assert any(item.water_temperature_c is None for item in result)
    assert all(item.place.timezone and item.is_synthetic and item.risks for item in result)
    assert json.loads(result[0].model_dump_json())["estimated_total_price_rub"] is None


async def test_budget_never_promises_a_trip_with_pending_prices():
    assert await DestinationTool().run(DestinationQuery(month=11, origin="LED", budget_rub=99999999)) == []


async def test_hard_filters_only_return_known_warm_water_and_visa_exemptions():
    result = await DestinationTool().run(
        DestinationQuery(
            month=11,
            origin="MOW",
            min_water_temperature_c=24,
            visa_free_only=True,
        )
    )
    assert result
    assert all(item.water_temperature_c >= 24 and item.visa_required is False for item in result)


async def test_root_override_and_explicit_root_use_saved_catalogue(tmp_path, monkeypatch):
    shutil.copy(DEFAULT_DATA_DIR / "destinations.yaml", tmp_path / "destinations.yaml")
    expected = load_catalogue(tmp_path / "destinations.yaml")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for tool in (DestinationTool(), DestinationTool(data_dir=tmp_path)):
        result = await tool.run(DestinationQuery(month=11, origin="LED"))
        assert {item.place.id for item in result} == {item.id for item in expected.destinations if item.iata != "LED"}


async def test_unsupported_origin_is_explicit_instead_of_silently_using_moscow():
    with pytest.raises(ValueError, match="Supported origin"):
        await DestinationTool().run(DestinationQuery(month=11, origin="JFK"))


async def test_unknown_visa_is_not_projected_as_visa_free():
    data = load_catalogue().model_dump(mode="json")
    record = data["destinations"][0]
    record["visa"].update(
        regime="unknown",
        verification_status="unknown",
        max_stay_days=None,
        rolling_window_days=None,
        checked_on=None,
        source=None,
    )
    tool = DestinationTool(catalogue=Catalogue.model_validate(data))
    result = await tool.run(DestinationQuery(month=11, origin="MOW"))
    assert next(item for item in result if item.place.id == record["id"]).visa_required is None
