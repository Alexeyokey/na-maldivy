import json
import shutil
from pathlib import Path

import pytest

from app.contracts.tools import HotelSearch
from app.data_paths import DEFAULT_DATA_DIR
from app.tools.hotels import HotelSearchTool, search_hotel_results


@pytest.mark.parametrize(
    ("adults", "ages", "rooms"),
    [
        (2, [5, 8], 1),
        (2, [0, 1, 5, 8], 1),
        (2, [2, 5, 8], 2),
        (4, [], 2),
        (3, [1], 2),
    ],
)
async def test_family_room_boundaries(adults, ages, rooms):
    result = await search_hotel_results(
        HotelSearch(
            destination="AYT",
            check_in="2026-11-18",
            check_out="2026-11-25",
            travelers={"adults": adults, "children_ages": ages},
        )
    )
    assert result
    assert all(item.rooms == rooms for item in result)
    assert all(item.offer.name == item.facts.name for item in result)


async def test_catalogue_rules_and_aliases_are_utf8_and_share_configured_root(tmp_path, monkeypatch):
    shutil.copytree(DEFAULT_DATA_DIR / "hotels", tmp_path / "hotels")
    path = tmp_path / "hotels/catalog.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for record in data["hotels"]:
        record["name"] = "Отель مرحبا"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    read_text = Path.read_text

    def windows_read_text(path, encoding=None, **kwargs):
        # Simulate a Windows installation whose locale codec cannot decode UTF-8.
        return read_text(path, encoding=encoding or "ascii", **kwargs)

    monkeypatch.setattr(Path, "read_text", windows_read_text)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for tool in (HotelSearchTool(), HotelSearchTool(data_dir=tmp_path)):
        result = await tool.run(HotelSearch(destination="DXB", check_in="2026-11-18", check_out="2026-11-25"))
        assert result and all(item.name == "Отель مرحبا" for item in result)
