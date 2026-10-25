import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.tools.destinations import (
    Catalogue,
    Preferences,
    UnknownDestinationError,
    clear_catalogue_cache,
    load_catalogue,
    resolve_iata,
    suggest_destinations,
)


def destination(
    identifier: str = "warm-bay",
    iata: str = "AAA",
    *,
    air: float | None = 27,
    water: float | None = 26,
    rain: float | None = 40,
) -> dict:
    return {
        "id": identifier,
        "name": "Тёплая бухта" if identifier == "warm-bay" else identifier,
        "country": "Страна",
        "country_code": "XX",
        "iata": iata,
        "aliases": [identifier, "Общее имя", "Warm Bay"] if identifier == "warm-bay" else ["Общее имя"],
        "location": {"latitude": 12.5, "longitude": 65.5, "timezone": "Asia/Dubai"},
        "geo_source": "geography",
        "tags": ["beach", "family"],
        "climate": {
            "air_source": "climate",
            "water_source": "climate" if water is not None else None,
            "months": [
                {"month": month, "air_temp_c": air, "water_temp_c": water, "precipitation_mm": rain}
                for month in range(1, 13)
            ],
            "notes": "Синтетическая фикстура теста; не фактический климат.",
        },
        "visa": {
            "citizenship": "RU",
            "passport_type": "ordinary",
            "purpose": "tourism",
            "regime": "unknown",
            "verification_status": "unknown",
            "effective_from": None,
            "effective_until": None,
            "max_stay_days": None,
            "rolling_window_days": None,
            "checked_on": None,
            "source": None,
            "conditions": "Не проверено.",
        },
        "prices": {
            "round_trip_flight_rub": {"MOW": None, "LED": None},
            "hotel_night_rub": None,
            "status": "pending",
            "source": None,
            "notes": "Ждём D2/D3.",
        },
        "flight_time_estimates": {
            "MOW": {"minutes": 300, "method": "Синтетическая тестовая оценка"},
            "LED": {"minutes": 360, "method": "Синтетическая тестовая оценка"},
        },
    }


@pytest.fixture
def raw_catalogue() -> dict:
    source = {
        "title": "Тестовый источник",
        "url": "https://example.org/dataset",
        "license": None,
        "retrieved_at": "2026-10-25",
        "period": "1991–2020",
        "method": "Synthetic local test data, not external facts",
    }
    inland = destination("inland", "CCC", water=None, rain=None)
    inland["tags"] = ["mountains", "sightseeing"]
    return {
        "schema_version": 1,
        "as_of": "2026-10-25",
        "sources": {"geography": source, "climate": source, "visa": source, "prices": source},
        "destinations": [destination(), destination("cold-bay", "BBB", water=18), inland],
    }


def write_catalogue(path: Path, raw: dict) -> Path:
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_source_details_are_available_in_json(raw_catalogue):
    response = suggest_destinations(11, catalogue=raw_catalogue)
    assert response.model_dump(mode="json")["sources"]["climate"]["url"] == "https://example.org/dataset"


def test_visa_period_must_cover_whole_trip(raw_catalogue):
    visa = raw_catalogue["destinations"][0]["visa"]
    visa.update(
        regime="exempt",
        verification_status="official_verified",
        source="visa",
        checked_on="2026-10-25",
        effective_from="2026-11-01",
        effective_until="2026-11-20",
        max_stay_days=30,
    )
    good = suggest_destinations(
        11,
        {"visa_free_only": True},
        departure_date=date(2026, 11, 1),
        nights=7,
        catalogue=raw_catalogue,
    )
    assert [item.destination.id for item in good.candidates] == ["warm-bay"]
    bad = suggest_destinations(
        11,
        {"visa_free_only": True},
        departure_date=date(2026, 11, 15),
        nights=7,
        catalogue=raw_catalogue,
    )
    assert "visa_rules_not_effective_for_trip" in next(
        item.reasons for item in bad.excluded if item.destination_id == "warm-bay"
    )
    with pytest.raises(ValidationError):
        suggest_destinations(11, departure_date=date(2027, 1, 1), catalogue=raw_catalogue)


def verified_visa(record: dict, **overrides: object) -> None:
    record["visa"].update(
        regime="exempt",
        verification_status="official_verified",
        max_stay_days=30,
        checked_on="2026-10-25",
        source="visa",
    )
    record["visa"].update(overrides)


def priced(record: dict, *, flight_mow: int = 20_000, flight_led: int | None = 25_000, hotel: int = 5_000) -> None:
    record["prices"].update(
        round_trip_flight_rub={"MOW": flight_mow, "LED": flight_led},
        hotel_night_rub=hotel,
        status="verified",
        source="prices",
    )


def test_warm_sea_query_excludes_cold_and_unknown_instead_of_inventing_values(raw_catalogue: dict) -> None:
    result = suggest_destinations(11, {"min_water_temp_c": 24}, catalogue=raw_catalogue)

    assert [item.destination.id for item in result.candidates] == ["warm-bay"]
    assert {item.destination_id: item.reasons for item in result.excluded} == {
        "cold-bay": ("water_too_cold",),
        "inland": ("unknown_water_temperature",),
    }
    assert result.candidates[0].climate.month == 11
    assert result.candidates[0].climate.water_temp_c == 26


def test_budget_does_not_pass_unknown_prices_even_when_large(raw_catalogue: dict) -> None:
    result = suggest_destinations(11, budget=10_000_000, catalogue=raw_catalogue)

    assert result.candidates == ()
    assert result.matched_count == 0
    assert all(item.reasons == ("unknown_price",) for item in result.excluded)


def test_budget_group_estimate_counts_nights_rooms_and_origin_once(raw_catalogue: dict) -> None:
    priced(raw_catalogue["destinations"][0])
    # Three return fares + 2 rooms * 7 nights * 5000 = 130000.
    equal = suggest_destinations(11, origin="SVO", budget=130_000, travelers=3, catalogue=raw_catalogue)
    lower = suggest_destinations(11, budget=129_999, travelers=3, catalogue=raw_catalogue)
    led = suggest_destinations(11, origin="LED", budget=130_000, travelers=3, catalogue=raw_catalogue)

    assert equal.candidates[0].trip_cost_estimate_rub == 130_000
    assert equal.query.origin == "MOW"
    assert "over_budget" in next(item for item in lower.excluded if item.destination_id == "warm-bay").reasons
    assert "over_budget" in next(item for item in led.excluded if item.destination_id == "warm-bay").reasons


def test_synthetic_and_incomplete_prices_cannot_meet_budget(raw_catalogue: dict) -> None:
    priced(raw_catalogue["destinations"][0])
    raw_catalogue["destinations"][0]["prices"]["status"] = "synthetic"
    priced(raw_catalogue["destinations"][1], flight_led=None)

    result = suggest_destinations(11, origin="LED", budget=1_000_000, catalogue=raw_catalogue)

    assert result.candidates == ()
    assert all("unknown_price" in item.reasons for item in result.excluded)


@pytest.mark.parametrize("regime", ["unknown", "on_arrival", "evisa", "required"])
def test_visa_free_filter_never_passes_unknown_or_visa_on_arrival(raw_catalogue: dict, regime: str) -> None:
    record = raw_catalogue["destinations"][0]
    if regime != "unknown":
        verified_visa(record, regime=regime)
    result = suggest_destinations(11, {"visa_free_only": True}, catalogue=raw_catalogue)

    assert result.candidates == ()
    assert result.excluded


def test_visa_exemption_requires_official_verification(raw_catalogue: dict) -> None:
    verified_visa(raw_catalogue["destinations"][0], verification_status="partial")

    result = suggest_destinations(11, {"visa_free_only": True}, catalogue=raw_catalogue)

    assert result.candidates == ()
    assert (
        "unverified_visa_rules" in next(item for item in result.excluded if item.destination_id == "warm-bay").reasons
    )


@pytest.mark.parametrize(
    "dates",
    [
        {"effective_until": "2026-10-24"},
        {"effective_from": "2026-10-26"},
    ],
)
def test_visa_effective_dates_are_checked_against_snapshot(raw_catalogue: dict, dates: dict) -> None:
    verified_visa(raw_catalogue["destinations"][0], **dates)
    result = suggest_destinations(11, {"visa_free_only": True}, catalogue=raw_catalogue)

    assert result.candidates == ()
    assert (
        "visa_rules_not_effective_at_snapshot"
        in next(item for item in result.excluded if item.destination_id == "warm-bay").reasons
    )


def test_visa_exemption_stay_limit_and_snapshot_boundaries(raw_catalogue: dict) -> None:
    verified_visa(
        raw_catalogue["destinations"][0],
        max_stay_days=8,
        effective_from="2026-10-25",
        effective_until="2026-10-25",
    )
    fits = suggest_destinations(11, {"visa_free_only": True}, nights=7, catalogue=raw_catalogue)
    too_long = suggest_destinations(11, {"visa_free_only": True}, nights=8, catalogue=raw_catalogue)

    assert [item.destination.id for item in fits.candidates] == ["warm-bay"]
    assert too_long.candidates == ()
    assert (
        "stay_exceeds_visa_limit"
        in next(item for item in too_long.excluded if item.destination_id == "warm-bay").reasons
    )


def test_domestic_travel_passes_without_tourist_visa(raw_catalogue: dict) -> None:
    verified_visa(raw_catalogue["destinations"][0], regime="domestic", verification_status="domestic")

    result = suggest_destinations(11, {"visa_free_only": True}, catalogue=raw_catalogue)

    assert [item.destination.id for item in result.candidates] == ["warm-bay"]


def test_tags_are_required_and_comfort_score_is_explained(raw_catalogue: dict) -> None:
    result = suggest_destinations(11, Preferences(tags=("beach", "family", "beach")), catalogue=raw_catalogue)

    assert result.matched_count == 2
    assert result.candidates[0].destination.id == "warm-bay"
    assert result.candidates[0].score == 25
    assert len(result.candidates[0].reasons) == 5
    assert "missing_required_tags" in result.excluded[0].reasons


def test_flight_estimate_filter_uses_origin_and_rejects_unknown(raw_catalogue: dict) -> None:
    raw_catalogue["destinations"][1]["flight_time_estimates"]["MOW"] = None
    mow = suggest_destinations(11, {"max_flight_minutes": 300}, origin="DME", catalogue=raw_catalogue)
    led = suggest_destinations(11, {"max_flight_minutes": 300}, origin="LED", catalogue=raw_catalogue)

    assert [item.destination.id for item in mow.candidates] == ["warm-bay", "inland"]
    assert mow.excluded[0].reasons == ("unknown_flight_duration",)
    assert led.candidates == ()
    assert all(item.reasons == ("flight_too_long",) for item in led.excluded)
    assert "пересадок не проверено" in mow.candidates[0].warnings[0]


def test_origin_city_not_recommended_as_flight_destination(raw_catalogue: dict) -> None:
    record = raw_catalogue["destinations"][0]
    record["iata"] = "LED"
    record["flight_time_estimates"]["LED"] = None

    led = suggest_destinations(11, origin="LED", catalogue=raw_catalogue)
    mow = suggest_destinations(11, origin="MOW", catalogue=raw_catalogue)

    assert "warm-bay" not in {item.destination.id for item in led.candidates}
    assert next(item for item in led.excluded if item.destination_id == "warm-bay").reasons == ("same_as_origin",)
    assert "warm-bay" in {item.destination.id for item in mow.candidates}


def test_rain_filter_and_risks_use_climate_normals_not_forecasts(raw_catalogue: dict) -> None:
    for month in raw_catalogue["destinations"][0]["climate"]["months"]:
        month.update(air_temp_c=35, precipitation_mm=150)
    result = suggest_destinations(11, catalogue=raw_catalogue)
    wet = next(item for item in result.candidates if item.destination.id == "warm-bay")
    filtered = suggest_destinations(11, {"max_precipitation_mm": 100}, catalogue=raw_catalogue)

    assert {risk.code for risk in wet.risks} == {"wet_month", "hot_month"}
    assert all("месяц" in risk.message or "Месячная" in risk.message for risk in wet.risks)
    assert {item.destination_id: item.reasons for item in filtered.excluded} == {
        "inland": ("unknown_precipitation",),
        "warm-bay": ("too_much_precipitation",),
    }


def test_limit_stable_tiebreak_and_input_mapping_not_modified(raw_catalogue: dict) -> None:
    before = deepcopy(raw_catalogue)
    first = suggest_destinations(11, limit=1, catalogue=raw_catalogue)
    second = suggest_destinations(11, limit=1, catalogue=raw_catalogue)

    assert first == second
    assert first.matched_count == 3
    assert len(first.candidates) == 1
    assert raw_catalogue == before
    raw_catalogue["destinations"].reverse()
    assert suggest_destinations(11, limit=1, catalogue=raw_catalogue) == first


@pytest.mark.parametrize(
    "kwargs",
    [
        {"month": 0},
        {"month": 13},
        {"month": True},
        {"month": "11"},
        {"budget": -1},
        {"budget": True},
        {"origin": "DXB"},
        {"nights": 0},
        {"travelers": 0},
        {"limit": 0},
        {"preferences": {"min_water_temp_c": float("nan")}},
        {"preferences": {"tags": ["not-a-tag"]}},
        {"preferences": {"min_water_temp": 24}},
    ],
)
def test_invalid_query_rejected_before_search(raw_catalogue: dict, kwargs: dict) -> None:
    with pytest.raises((ValidationError, ValueError)):
        suggest_destinations(**({"month": 11, "catalogue": raw_catalogue} | kwargs))


def test_resolve_alias_normalization_ambiguity_and_absence(raw_catalogue: dict) -> None:
    assert resolve_iata("  ТЕПЛАЯ   БУХТА  ", catalogue=raw_catalogue) == ("AAA",)
    assert resolve_iata("aaa", catalogue=raw_catalogue) == ("AAA",)
    assert resolve_iata("warm bay", catalogue=raw_catalogue) == ("AAA",)
    assert resolve_iata("Общее имя", catalogue=raw_catalogue) == ("AAA", "BBB", "CCC")
    with pytest.raises(UnknownDestinationError):
        resolve_iata("Неизвестный город", catalogue=raw_catalogue)
    with pytest.raises(ValueError):
        resolve_iata(" ", catalogue=raw_catalogue)


@pytest.mark.parametrize("field", ["id", "iata"])
def test_catalogue_rejects_duplicate_identity(raw_catalogue: dict, field: str) -> None:
    raw_catalogue["destinations"][1][field] = raw_catalogue["destinations"][0][field]
    with pytest.raises(ValidationError, match=f"Duplicate destination {field}"):
        Catalogue.model_validate(raw_catalogue)


@pytest.mark.parametrize("broken", ["missing_month", "duplicate_month", "unordered", "missing_source", "typo"])
def test_catalogue_rejects_broken_months_and_provenance(raw_catalogue: dict, broken: str) -> None:
    climate = raw_catalogue["destinations"][0]["climate"]
    if broken == "missing_month":
        climate["months"].pop()
    elif broken == "duplicate_month":
        climate["months"][1]["month"] = 1
    elif broken == "unordered":
        climate["months"].reverse()
    elif broken == "missing_source":
        climate["water_source"] = "no-such-source"
    else:
        climate["rain_mm"] = 10
    with pytest.raises(ValidationError):
        Catalogue.model_validate(raw_catalogue)


def test_known_climate_values_require_explicit_provenance(raw_catalogue: dict) -> None:
    raw_catalogue["destinations"][0]["climate"]["water_source"] = None
    with pytest.raises(ValidationError, match="requires water_source"):
        Catalogue.model_validate(raw_catalogue)


def test_catalogue_rejects_future_snapshot_claims(raw_catalogue: dict) -> None:
    raw_catalogue["sources"]["geography"]["retrieved_at"] = "2026-10-26"
    with pytest.raises(ValidationError, match="retrieved_at"):
        Catalogue.model_validate(raw_catalogue)


def test_catalogue_safe_load_cache_env_override_immutability_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw_catalogue: dict
) -> None:
    path = write_catalogue(tmp_path / "catalogue.yaml", raw_catalogue)
    monkeypatch.setenv("DESTINATIONS_DATA_PATH", str(path))
    clear_catalogue_cache()

    data = load_catalogue()
    assert data is load_catalogue(path)
    assert data.as_of == date(2026, 10, 25)
    with pytest.raises(TypeError):
        data.sources["other"] = data.sources["climate"]
    with pytest.raises(ValidationError):
        data.destinations[0].climate.months[0].water_temp_c = 99
    assert json.loads(data.model_dump_json())["sources"]["geography"]["url"] == "https://example.org/dataset"
    path.write_text("not: a valid catalogue", encoding="utf-8")
    assert load_catalogue(path) is data
    clear_catalogue_cache()
    with pytest.raises(ValidationError):
        load_catalogue(path)


def test_yaml_object_execution_not_allowed(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.yaml"
    path.write_text("!!python/object/apply:builtins.print ['should never run']", encoding="utf-8")
    with pytest.raises(yaml.constructor.ConstructorError):
        load_catalogue(path)


def test_schema_dump_and_response_keep_provenance(raw_catalogue: dict) -> None:
    result = suggest_destinations(11, catalogue=raw_catalogue)

    assert Catalogue.model_json_schema()["properties"]["schema_version"]["const"] == 1
    output = json.loads(result.model_dump_json())
    assert output["candidates"][0]["destination"]["climate"]["water_source"] == "climate"
    assert output["catalogue_as_of"] == "2026-10-25"
    assert output["candidates"][0]["trip_cost_estimate_rub"] is None


def test_catalogue_and_path_are_mutually_exclusive(raw_catalogue: dict) -> None:
    with pytest.raises(ValueError, match="either catalogue or data_path"):
        suggest_destinations(11, catalogue=raw_catalogue, data_path="ignored.yaml")
