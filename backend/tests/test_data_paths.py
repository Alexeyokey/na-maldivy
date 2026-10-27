import json
import shutil

from app.config import Settings
from app.contracts.tools import TransferRequest
from app.data_paths import DEFAULT_DATA_DIR, resolve_data_dir
from app.tools.s4_adapter import S4TransferTool
from app.tools.transfers import default_base


def test_default_root_is_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert Settings(_env_file=None).data_dir == DEFAULT_DATA_DIR
    assert resolve_data_dir() == DEFAULT_DATA_DIR


async def test_custom_root_controls_both_transfer_tariffs_and_places(tmp_path, monkeypatch):
    root = tmp_path / "custom-data"
    shutil.copytree(DEFAULT_DATA_DIR / "transfers", root / "transfers")
    shutil.copytree(DEFAULT_DATA_DIR / "hotels", root / "hotels")
    tariffs_path = root / "transfers/transfers.json"
    tariffs = json.loads(tariffs_path.read_text(encoding="utf-8"))
    record = tariffs["transfers"][0]
    for option in record["options"]:
        option["pricing"]["price_rub"] = 765432
    tariffs_path.write_text(json.dumps(tariffs), encoding="utf-8")
    hotels_path = root / "hotels/osm_hotels.json"
    hotels = json.loads(hotels_path.read_text(encoding="utf-8"))
    next(h for h in hotels["hotels"] if h["hotel_id"] == record["hotel_id"])["name"] = "Другой каталог"
    hotels_path.write_text(json.dumps(hotels, ensure_ascii=False), encoding="utf-8")
    # Warm the ordinary cache first: the second root must never reuse it.
    original = default_base(DEFAULT_DATA_DIR)
    monkeypatch.setenv("DATA_DIR", str(root))
    settings = Settings(_env_file=None)
    assert settings.data_dir == root
    assert default_base() is not original
    query = TransferRequest(airport=record["airport"], hotel_id=record["hotel_id"], pickup_at="2026-11-18T09:00:00Z")
    for tool in (S4TransferTool(), S4TransferTool(data_dir=settings.data_dir)):
        quotes = await tool.run(query)
        assert all(q.destination.name == "Другой каталог" for q in quotes)
        assert all(q.price_rub >= 765432 for q in quotes)
