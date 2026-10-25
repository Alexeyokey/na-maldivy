"""Приёмочные проверки сохранённых фактов D5, сборки и конечного подбора."""

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from app.tools.destinations import load_catalogue, resolve_iata, suggest_destinations

ROOT = Path(__file__).resolve().parents[2]


def test_catalogue_has_48_sourced_year_round_records() -> None:
    catalogue = load_catalogue()
    assert len(catalogue.destinations) == 48
    assert len({record.id for record in catalogue.destinations}) == 48
    assert len({record.iata for record in catalogue.destinations}) == 48
    for record in catalogue.destinations:
        assert record.geo_source == "mwgg-airports"
        assert catalogue.sources[record.climate.air_source].period == "1991-2020"
        assert len(record.climate.months) == 12
        assert all(
            month.air_temp_c is not None and month.precipitation_mm is not None for month in record.climate.months
        )
    beaches = [record for record in catalogue.destinations if "beach" in record.tags]
    assert len(beaches) == 27
    assert all(all(month.water_temp_c is not None for month in record.climate.months) for record in beaches)


def test_november_warm_sea_excludes_cold_coasts_and_inland_places() -> None:
    result = suggest_destinations(11, {"tags": ["beach"], "min_water_temp_c": 24}, limit=100)
    codes = {item.destination.iata for item in result.candidates}
    assert {"DXB", "MLE", "HRG", "HKT"} <= codes
    assert not {"AER", "AYT", "KZN", "MRV"} & codes
    assert result.matched_count >= 10
    assert all(item.climate.water_temp_c >= 24 for item in result.candidates)


def test_actual_visa_sources_never_turn_unknown_into_exempt() -> None:
    data = load_catalogue()
    thailand = next(record for record in data.destinations if record.iata == "HKT")
    vietnam = next(record for record in data.destinations if record.iata == "DAD")
    assert thailand.visa.max_stay_days == 30
    assert thailand.visa.effective_from.isoformat() == "2026-09-15"
    assert vietnam.visa.max_stay_days == 45
    assert vietnam.visa.effective_until.isoformat() == "2028-03-14"
    result = suggest_destinations(11, {"min_water_temp_c": 24, "visa_free_only": True}, limit=100)
    assert all(item.destination.visa.regime in {"exempt", "domestic"} for item in result.candidates)
    assert all(
        item.destination.visa.verification_status in {"official_verified", "domestic"} for item in result.candidates
    )
    assert "MLE" in {item.destination.iata for item in result.candidates}
    assert not {"DPS", "CMB", "HRG", "ZNZ"} & {item.destination.iata for item in result.candidates}


def test_unknown_real_prices_cannot_satisfy_a_budget() -> None:
    result = suggest_destinations(11, budget=10_000_000, limit=100)
    assert result.candidates == ()
    assert len(result.excluded) == 48
    assert all("unknown_price" in item.reasons for item in result.excluded)


def test_expired_rule_is_disclosed_even_without_strict_visa_filter() -> None:
    result = suggest_destinations(11, departure_date=date(2030, 11, 15), limit=100)
    vietnam = next(item for item in result.candidates if item.destination.iata == "DAD")
    assert any("применимость не подтверждена" in warning for warning in vietnam.warnings)


def test_flight_estimates_differ_by_origin_and_distance() -> None:
    data = load_catalogue()
    dubai = next(record for record in data.destinations if record.iata == "DXB")
    bali = next(record for record in data.destinations if record.iata == "DPS")
    assert 250 < dubai.flight_time_estimates.MOW.minutes < 450
    assert dubai.flight_time_estimates.LED.minutes > dubai.flight_time_estimates.MOW.minutes
    assert bali.flight_time_estimates.MOW.minutes > dubai.flight_time_estimates.MOW.minutes + 300
    assert "Not a timetable" in dubai.flight_time_estimates.MOW.method
    result = suggest_destinations(11, {"max_flight_minutes": 300}, origin="LED", limit=100)
    assert all(item.flight_minutes_estimate <= 300 for item in result.candidates)
    assert "LED" not in {item.destination.iata for item in result.candidates}


@pytest.mark.parametrize("alias,code", [("Анталья", "AYT"), ("Питер", "LED"), ("Бишкек", "BSZ"), ("FRU", "BSZ")])
def test_real_city_and_legacy_airport_aliases(alias: str, code: str) -> None:
    assert resolve_iata(alias) == (code,)


def test_built_catalogue_matches_committed_source_snapshot() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_destinations.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_uses_bundled_snapshot_without_system_timezone_database() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.destination_cli", "--month", "11", "--tag", "beach", "--min-water", "24", "--json"],
        cwd=ROOT / "backend",
        env=dict(os.environ, PYTHONTZPATH=""),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["matched_count"] > 0
    assert all(item["climate"]["water_temp_c"] >= 24 for item in response["candidates"])
