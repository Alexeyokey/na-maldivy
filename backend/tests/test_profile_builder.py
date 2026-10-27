import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts.hotel_profiles import HotelReviewProfile
from app.contracts.tools import HotelOffer, HotelSearch
from app.data_paths import DEFAULT_DATA_DIR
from app.tools.hotel_profiles import load_hotel_profile
from app.tools.hotels import HotelSearchTool
from app.tools.registry import run_tool

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/build_hotel_profiles.py"


def hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    }


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=ROOT, capture_output=True, text=True)


def test_check_is_read_only_and_validates_all_sixty_profiles():
    before = hashes(DEFAULT_DATA_DIR)
    result = run("--check")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["profiles"] == 60
    assert json.loads(result.stdout)["live_model_calls"] == 0
    assert hashes(DEFAULT_DATA_DIR) == before


def test_review_generation_never_rewrites_hotel_rules_or_osm_facts(tmp_path):
    shutil.copytree(DEFAULT_DATA_DIR / "hotels", tmp_path / "hotels")
    before = hashes(tmp_path / "hotels")
    result = run("--data-dir", tmp_path, "--generate-reviews")
    assert result.returncode == 0, result.stderr
    assert hashes(tmp_path / "hotels") == before
    assert len(list((tmp_path / "hotel_reviews").glob("*.json"))) == 60
    assert len(list((tmp_path / "hotel_profiles").glob("*.json"))) == 60
    assert run("--data-dir", tmp_path, "--check").returncode == 0


@pytest.mark.parametrize("args", [("--mode", "llm"), ("--check", "--generate-reviews"), ("--hotel-id", "unknown")])
def test_invalid_or_unapproved_llm_operation_changes_nothing(args):
    before = hashes(DEFAULT_DATA_DIR)
    result = run(*args)
    assert result.returncode == 2
    assert hashes(DEFAULT_DATA_DIR) == before


async def test_d1_dispatch_includes_validated_profile_for_every_hotel():
    tool = HotelSearchTool()
    for destination in ("AYT", "DXB", "MLE"):
        offers = await run_tool(
            tool.name,
            HotelSearch(destination=destination, check_in="2026-11-18", check_out="2026-11-25").model_dump(),
            tools={tool.name: tool},
        )
        assert len(offers) == 20
        for offer in offers:
            assert offer.review_profile.hotel_id == offer.hotel_id
            assert offer.review_profile.review_count == 24
            assert offer.review_profile.is_synthetic
            assert not offer.review_profile.extraction.live_model_run
            assert HotelOffer.model_validate_json(offer.model_dump_json()) == offer


@pytest.mark.parametrize("change", ["count", "segment_count", "identity", "live_claim"])
def test_tampered_counts_id_or_provenance_are_rejected(change):
    path = next((DEFAULT_DATA_DIR / "hotel_profiles").glob("*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    if change == "count":
        data["aspects"]["wifi"]["mention_count"] += 1
    if change == "segment_count":
        data["guest_profiles"]["family"]["review_count"] += 1
    if change == "identity":
        data["hotel_id"] = "osm:node/999999999999"
    if change == "live_claim":
        data["extraction"]["live_model_run"] = True
    with pytest.raises(ValidationError):
        HotelReviewProfile.model_validate(data)


def test_profile_cache_returns_an_independent_object_and_unknown_stays_unknown(tmp_path):
    path = next((DEFAULT_DATA_DIR / "hotel_profiles").glob("*.json"))
    hotel_id = json.loads(path.read_text(encoding="utf-8"))["hotel_id"]
    profile = load_hotel_profile(hotel_id)
    profile.aspects.clear()
    assert load_hotel_profile(hotel_id).aspects
    assert load_hotel_profile(hotel_id, data_dir=tmp_path) is None
