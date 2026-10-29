"""Fictional supplements cover D5 without changing OSM evidence or source modes."""

import hashlib
import importlib.util
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.contracts.hotel_profiles import HotelReviewProfile
from app.contracts.tools import HotelSearch
from app.tools.destinations import load_catalogue
from app.tools.expanded_hotels import (
    ExpandedHotelCatalogue,
    ExpandedHotelRecord,
    expanded_hotel_records,
    expanded_hotel_rules,
    load_expanded_catalogue,
)
from app.tools.hotel_profiles import SyntheticReview, build_profile, extract_lexical, load_hotel_profile
from app.tools.hotels import (
    HotelSearchTool,
    get_synthetic_rules,
    hotel_id_for_offer,
    load_hotel_catalog,
    normalize_hotel_id,
    search_hotel_results,
    search_hotels,
)
from app.u2_eval import forbid_network

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SPEC = importlib.util.spec_from_file_location("demo_hotels_builder", ROOT / "scripts/build_demo_hotels.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
NEW_DESTINATIONS = sorted(
    {row.iata for row in load_catalogue(DATA / "destinations.yaml").destinations} - {"AYT", "DXB", "MLE"}
)


def snapshots(paths):
    return {path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in paths}


def query(destination="CMB", **overrides):
    return HotelSearch.model_validate(
        {"destination": destination, "check_in": "2026-11-18", "check_out": "2026-11-25", **overrides}
    )


def test_fictional_generator_is_exact_offline_and_preserves_every_original_source_file():
    original = [
        path
        for directory in ("hotels", "hotel_reviews", "hotel_profiles")
        for path in (DATA / directory).glob("*.json")
    ]
    before = snapshots(original)
    generated = [DATA / relative for relative in BUILDER.build_documents(DATA)]
    generated_before = snapshots(generated)
    with forbid_network():
        report = BUILDER.run(DATA, check=True)
        repeated = BUILDER.build_documents(DATA)
    assert report == {
        "status": "passed",
        "destinations": 45,
        "hotels": 360,
        "reviews": 8640,
        "profiles": 360,
        "read_only_check": True,
        "live_model_calls": 0,
        "network_calls": 0,
        "source_kind": "fictional_demo",
    }
    assert all((DATA / relative).read_bytes() == content for relative, content in repeated.items())
    assert snapshots(original) == before
    assert snapshots(generated) == generated_before


def test_catalogue_keeps_osm_and_fictional_identity_provenance_and_modes_separate():
    original = load_hotel_catalog(data_dir=DATA)
    assert len(original) == 60
    assert all(row.hotel_id.startswith("osm:") for row in original)
    assert HotelSearchTool(data_dir=DATA).supported_destinations == {"AYT", "DXB", "MLE"}
    expanded = load_hotel_catalog(data_dir=DATA, include_demo=True)
    assert len(expanded) == 420
    assert expanded[:60] == original
    assert len(NEW_DESTINATIONS) == 45 and "LED" in NEW_DESTINATIONS
    assert HotelSearchTool(data_dir=DATA, include_demo=True).supported_destinations == {
        row.iata for row in load_catalogue(DATA / "destinations.yaml").destinations
    }
    for destination in NEW_DESTINATIONS:
        assert load_hotel_catalog(destination, data_dir=DATA) == []
        records = load_hotel_catalog(destination, data_dir=DATA, include_demo=True)
        assert len(records) == 8
        for record in records:
            assert isinstance(record, ExpandedHotelRecord)
            assert record.is_fictional and record.source_kind == "fictional_demo"
            assert record.coordinate_kind == "synthetic_offset_from_airport"
            assert record.hotel_id.startswith(f"demo:hotel/{destination}-")
            assert record.name.startswith("Демо · ")
            assert not any(key.startswith("osm_") for key in record.model_dump())
            assert 3 <= record.stars <= 5


@pytest.mark.parametrize("destination", NEW_DESTINATIONS)
async def test_every_new_destination_has_real_pipeline_family_pet_refund_offers_and_review_evidence(destination):
    with forbid_network():
        results = await search_hotel_results(
            query(destination, travelers={"adults": 2, "children_ages": [0, 5, 5], "pets": 1}, refundable_only=True),
            data_dir=DATA,
            include_demo=True,
        )
    assert results
    assert f"demo:hotel/{destination}-01" in {result.hotel_id for result in results}
    for result in results:
        offer = result.offer
        assert not offer.adults_only and offer.pets_allowed and offer.refundable and offer.reception_24h
        assert offer.hotel_id == offer.place.id == result.hotel_id
        assert offer.is_synthetic and offer.review_profile.review_count == 24
        assert offer.place.timezone == result.facts.timezone
        assert result.facts_license_url is None
        assert "вымышленный" in result.attribution and "Вымышленный" in result.notice
        assert "OpenStreetMap" not in result.attribution
        assert hotel_id_for_offer(offer.offer_id, data_dir=DATA) == result.hotel_id
        profile = load_hotel_profile(result.hotel_id, data_dir=DATA)
        assert profile.hotel_id == result.hotel_id
        assert profile.extraction.live_model_run is False and profile.extraction.model is None
        assert profile.guest_profiles["family"].aspects["children"].positive_count == 1
        assert profile.guest_profiles["pets"].aspects["pets"].positive_count == 2


def test_all_360_profiles_exactly_match_literal_corpora_and_adults_only_and_pet_rules():
    for record in expanded_hotel_records(DATA):
        filename = record.hotel_id.replace(":", "-").replace("/", "-") + ".json"
        corpus = json.loads((DATA / "demo/hotel_reviews" / filename).read_bytes())
        assert corpus["is_synthetic"] is True and corpus["real_guest_reviews_used"] is False
        reviews = [SyntheticReview.model_validate(row) for row in corpus["reviews"]]
        assert len(reviews) == 24 and all(row.hotel_id == record.hotel_id for row in reviews)
        extracted = extract_lexical(reviews)
        assert all(row.mentions == review.annotations for row, review in zip(extracted, reviews, strict=True))
        profile = load_hotel_profile(record.hotel_id, data_dir=DATA)
        assert profile == HotelReviewProfile.model_validate(build_profile(reviews, extracted))
        rules = get_synthetic_rules(record.hotel_id, data_dir=DATA)
        children = profile.guest_profiles["family"].aspects["children"]
        pets = profile.guest_profiles["pets"].aspects["pets"]
        assert (children.positive_count, children.negative_count) == ((0, 1) if rules["adults_only"] else (1, 0))
        assert (pets.positive_count, pets.negative_count) == ((2, 0) if rules["pets_allowed"] else (0, 2))
        assert all(
            evidence.quote in next(row.text for row in reviews if row.review_id == evidence.review_id)
            for aspect in profile.aspects.values()
            for evidence in aspect.evidence
        )


async def test_legacy_mode_stays_empty_for_new_destinations_and_empty_budget_stays_honest():
    assert await search_hotels(query(), data_dir=DATA) == []
    assert await search_hotels(query(), data_dir=DATA, include_demo=True)
    assert await search_hotels(query(max_price_rub=0), data_dir=DATA, include_demo=True) == []
    assert await search_hotels(query("ZZZ"), data_dir=DATA, include_demo=True) == []
    originals = await search_hotels(query("AYT"), data_dir=DATA)
    assert await search_hotels(query("AYT"), data_dir=DATA, include_demo=True) == originals


@pytest.mark.parametrize("start,nights", [(date(2026, 12, 31), 1), (date(2027, 1, 29), 7), (date(2028, 2, 29), 60)])
async def test_safe_demo_inventory_is_stable_across_year_leap_and_long_stay_and_group_price(start, nights):
    search = query(
        check_in=start,
        check_out=start + timedelta(days=nights),
        travelers={"adults": 4, "children_ages": [0, 5, 5], "pets": 2},
        refundable_only=True,
    )
    results = await search_hotel_results(search, data_dir=DATA, include_demo=True)
    safe = next(row for row in results if row.hotel_id == "demo:hotel/CMB-01")
    assert safe.rooms == 2
    rules = safe.synthetic_rules
    factor = 90 + ((start.month * 7 + rules["seed"] % 11) % 5) * 10
    assert safe.offer.price_rub == rules["base_room_night_rub"] * factor // 100 * 2 * nights + 2000
    assert rules["always_available"] and rules["safe_rule"] == "family_pets_refundable_24h"
    assert await search_hotel_results(search, data_dir=DATA, include_demo=True) == results


def test_demo_ids_require_catalogue_membership_and_rules_profiles_are_defensive_copies():
    identity = "demo:hotel/CMB-01"
    assert normalize_hotel_id(identity, data_dir=DATA) == identity
    rules = get_synthetic_rules(identity, data_dir=DATA)
    rules["pets_allowed"] = False
    assert get_synthetic_rules(identity, data_dir=DATA)["pets_allowed"] is True
    profile = load_hotel_profile(identity, data_dir=DATA)
    profile.aspects["pets"].evidence.clear()
    assert load_hotel_profile(identity, data_dir=DATA).aspects["pets"].evidence
    for invalid in ("demo:hotel/ZZZ-01", "demo:hotel/CMB-99", "demo:hotel/CMB-00", "demo:hotel/../CMB-01"):
        with pytest.raises(ValueError):
            normalize_hotel_id(invalid, data_dir=DATA)
    assert load_hotel_profile("demo:hotel/ZZZ-01", data_dir=DATA) is None


def test_tools_use_only_their_configured_generated_catalogue_not_another_root(tmp_path):
    shutil.copytree(DATA / "hotels", tmp_path / "hotels")
    assert HotelSearchTool(data_dir=tmp_path, include_demo=True).supported_destinations == {"AYT", "DXB", "MLE"}
    assert load_hotel_catalog("CMB", data_dir=tmp_path, include_demo=True) == []
    assert load_hotel_profile("demo:hotel/CMB-01", data_dir=tmp_path) is None


@pytest.mark.parametrize("mutation", ["duplicate", "wrong_identity", "unsafe_always_available", "fake_osm"])
def test_generated_catalogue_rejects_ambiguous_or_misleading_records(mutation):
    document = json.loads((DATA / "demo/hotels.generated.json").read_bytes())
    row = document["hotels"][0]
    if mutation == "duplicate":
        document["hotels"].append(row)
    elif mutation == "wrong_identity":
        row["rules"]["hotel_id"] = "demo:hotel/CMB-01"
    elif mutation == "unsafe_always_available":
        row["rules"]["pets_allowed"] = False
    else:
        row["osm_id"] = 123
    with pytest.raises(ValueError):
        ExpandedHotelCatalogue.model_validate(document)


@pytest.mark.parametrize("mutation", ["corpus", "profile", "catalogue", "extra_corpus", "crlf"])
def test_read_only_check_rejects_byte_or_identity_drift_without_rewriting(tmp_path, mutation):
    for relative in ("destinations.yaml", "hotels/catalog.json"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DATA / relative, target)
    BUILDER.run(tmp_path)
    if mutation == "catalogue":
        path = tmp_path / "demo/hotels.generated.json"
    elif mutation == "profile":
        path = next((tmp_path / "demo/hotel_profiles").glob("*.json"))
    else:
        path = next((tmp_path / "demo/hotel_reviews").glob("*.json"))
    if mutation == "extra_corpus":
        path = path.parent / "unrecognized.json"
        path.write_bytes(b"{}\n")
    elif mutation == "crlf":
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    else:
        path.write_bytes(path.read_bytes() + b" ")
    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    before = snapshots(files)
    with pytest.raises(ValueError):
        with forbid_network():
            BUILDER.run(tmp_path, check=True)
    assert snapshots(files) == before


def test_expanded_helpers_do_not_read_environment_for_explicit_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert len(expanded_hotel_records(DATA)) == 360
    assert len(expanded_hotel_rules(DATA)) == 360
    assert len(load_expanded_catalogue(DATA).hotels) == 360
