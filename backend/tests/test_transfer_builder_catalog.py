"""Reject catalogue drift before it can invent island routes for AYT/DXB."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_transfers", ROOT / "backend/scripts/build_transfers.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    """Use checked-in source inputs; only the resulting file lives in tmp_path."""
    paths = {}
    for name in ("HOTELS_PATH", "AIRPORTS_PATH", "ROUTES_PATH"):
        original = getattr(BUILDER, name)
        path = tmp_path / original.name
        path.write_bytes(original.read_bytes())
        monkeypatch.setattr(BUILDER, name, path)
        paths[name] = path
    output = tmp_path / "transfers.json"
    monkeypatch.setattr(BUILDER, "TRANSFERS_PATH", output)
    return paths, output


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_offline_rebuild_preserves_all_published_transfer_options(snapshot):
    _, output = snapshot
    BUILDER.build()
    published = json.loads((ROOT / "data/transfers/transfers.json").read_text())
    rebuilt = json.loads(output.read_text())
    assert rebuilt == published
    assert len(rebuilt["transfers"]) == 60
    assert sum(len(record["options"]) for record in rebuilt["transfers"]) == 136


@pytest.mark.parametrize("airport", ["AYT", "DXB"])
def test_missing_road_route_cannot_fall_back_to_maldives_model(snapshot, airport):
    paths, output = snapshot
    routes = json.loads(paths["ROUTES_PATH"].read_text())
    hotel_id = next(hotel_id for hotel_id, route in routes["routes"].items() if route["airport"] == airport)
    del routes["routes"][hotel_id]
    write_json(paths["ROUTES_PATH"], routes)
    with pytest.raises(ValueError, match=f"Нет дорожного маршрута OSRM {airport}"):
        BUILDER.build()
    assert not output.exists()


def test_road_route_must_start_at_the_hotels_airport(snapshot):
    paths, output = snapshot
    routes = json.loads(paths["ROUTES_PATH"].read_text())
    next(iter(routes["routes"].values()))["airport"] = "MLE"
    write_json(paths["ROUTES_PATH"], routes)
    with pytest.raises(ValueError, match="относится к аэропорту MLE"):
        BUILDER.build()
    assert not output.exists()


def test_maldives_hotel_cannot_accidentally_use_the_road_model(snapshot):
    paths, output = snapshot
    hotels = json.loads(paths["HOTELS_PATH"].read_text())["hotels"]
    hotel_id = next(hotel["hotel_id"] for hotel in hotels if hotel["destination"] == "MLE")
    routes = json.loads(paths["ROUTES_PATH"].read_text())
    routes["routes"][hotel_id] = {"airport": "MLE", "duration_s": 600, "distance_m": 1000}
    write_json(paths["ROUTES_PATH"], routes)
    with pytest.raises(ValueError, match="Для MLE используется островная модель"):
        BUILDER.build()
    assert not output.exists()


def test_duplicate_hotel_identity_cannot_silently_overwrite_transfers(snapshot):
    paths, output = snapshot
    hotels = json.loads(paths["HOTELS_PATH"].read_text())
    hotels["hotels"].append(hotels["hotels"][0])
    write_json(paths["HOTELS_PATH"], hotels)
    with pytest.raises(ValueError, match="повторяющиеся hotel_id"):
        BUILDER.build()
    assert not output.exists()


def test_stale_route_cannot_outlive_its_hotel_catalogue_entry(snapshot):
    paths, output = snapshot
    hotels = json.loads(paths["HOTELS_PATH"].read_text())
    hotels["hotels"].pop(0)
    write_json(paths["HOTELS_PATH"], hotels)
    with pytest.raises(ValueError, match="вне каталога"):
        BUILDER.build()
    assert not output.exists()


def test_unknown_destination_cannot_be_treated_as_a_maldives_hotel(snapshot):
    paths, output = snapshot
    hotels = json.loads(paths["HOTELS_PATH"].read_text())
    hotels["hotels"][-1]["destination"] = "NRT"
    write_json(paths["HOTELS_PATH"], hotels)
    with pytest.raises(ValueError, match="Нет поддерживаемого аэропорта NRT"):
        BUILDER.build()
    assert not output.exists()
