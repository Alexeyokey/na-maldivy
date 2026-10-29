"""Synthetic reviews must not contradict the synthetic policies they illustrate."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent.brief import TripBrief
from app.agent.review_explanations import explain_hotel_reviews
from app.agent.session import Session
from app.contracts.hotel_profiles import HotelReviewProfile
from app.tools.hotel_profiles import build_profile, extract_lexical, load_hotel_profile, make_synthetic_reviews
from app.tools.hotels import DATA_ROOT, get_synthetic_rules, hotel_id_filename, load_hotel_catalog

ROOT = Path(__file__).resolve().parents[2]


def test_all_saved_profiles_agree_with_explicit_synthetic_children_and_pet_rules():
    hotels = load_hotel_catalog()
    adults_only = 0
    for hotel in hotels:
        rules = get_synthetic_rules(hotel.hotel_id)
        profile = load_hotel_profile(hotel.hotel_id)
        assert profile.review_count == 24
        assert profile.is_synthetic and not profile.extraction.live_model_run
        family = profile.guest_profiles["family"]
        children = family.aspects["children"]
        prohibited = rules["adults_only"]
        adults_only += prohibited
        assert (children.positive_count, children.negative_count) == ((0, 1) if prohibited else (1, 0)), hotel.hotel_id
        assert any(row.aspect == "children" for row in (family.cons if prohibited else family.pros))
        assert all(row.aspect != "children" for row in (family.pros if prohibited else family.cons))
        pets = profile.guest_profiles["pets"].aspects["pets"]
        assert (pets.positive_count, pets.negative_count) == ((2, 0) if rules["pets_allowed"] else (0, 2))
        document = json.loads((DATA_ROOT / "hotel_reviews" / hotel_id_filename(hotel.hotel_id)).read_text("utf-8"))
        originals = {row["review_id"]: row["text"] for row in document["reviews"]}
        for evidence in [*children.evidence, *pets.evidence]:
            assert evidence.quote in originals[evidence.review_id]
    assert len(hotels) == 60 and adults_only == 12


def test_changing_only_adults_only_changes_one_existing_review_and_its_evidence():
    hotel = load_hotel_catalog()[0]
    rules = get_synthetic_rules(hotel.hotel_id)
    allowed = make_synthetic_reviews(hotel.hotel_id, {**rules, "adults_only": False})
    prohibited = make_synthetic_reviews(hotel.hotel_id, {**rules, "adults_only": True})
    assert len(allowed) == len(prohibited) == 24
    assert [row.review_id for row in allowed] == [row.review_id for row in prohibited]
    changed = [(before, after) for before, after in zip(allowed, prohibited, strict=True) if before != after]
    assert len(changed) == 1
    before, after = changed[0]
    assert before.segment == after.segment == "family"
    assert after.review_id.endswith("/family/01")
    assert before.annotations[0].sentiment == "positive" and after.annotations[0].sentiment == "negative"
    assert "В модельном сценарии" in after.text and "не отзыв реального гостя" in after.text
    first = build_profile(allowed, extract_lexical(allowed))
    second = build_profile(prohibited, extract_lexical(prohibited))
    assert first["corpus_sha256"] != second["corpus_sha256"]
    assert first["extraction"] == second["extraction"]
    HotelReviewProfile.model_validate(second)


def test_reviews_do_not_invent_refund_terms_when_the_synthetic_tariff_changes():
    hotel = load_hotel_catalog()[0]
    rules = get_synthetic_rules(hotel.hotel_id)
    refundable = make_synthetic_reviews(hotel.hotel_id, {**rules, "refundable": True})
    nonrefundable = make_synthetic_reviews(hotel.hotel_id, {**rules, "refundable": False})
    assert refundable == nonrefundable
    assert not any("возврат" in row.text.casefold() or "отмен" in row.text.casefold() for row in refundable)


def test_an_adults_only_family_explanation_cannot_quote_children_suitability_as_a_pro():
    for hotel in load_hotel_catalog():
        if not get_synthetic_rules(hotel.hotel_id)["adults_only"]:
            continue
        profile = load_hotel_profile(hotel.hotel_id)
        session = Session(
            brief=TripBrief(adults=2),
            packages=[
                {
                    "index": 1,
                    "hotel": {
                        "hotel_id": hotel.hotel_id,
                        "name": hotel.name,
                        "adults_only": True,
                        "review_profile": profile.model_dump(mode="json"),
                    },
                }
            ],
        )
        result = explain_hotel_reviews(session, "Почему этот отель подходит семье?")
        assert result.facts["status"] == "available"
        children = next(row for row in result.facts["counts"] if row["aspect"] == "children")
        assert children["positive_count"] == 0 and children["negative_count"] == 1
        assert "детям было удобно играть" not in result.text
        assert "проживание с детьми запрещено" in result.text
        assert any(row["aspect"] == "children" and row["sentiment"] == "negative" for row in result.facts["evidence"])
        assert session.selected is None and session.booking is None


@pytest.mark.parametrize("directory", ["hotel_reviews", "hotel_profiles"])
@pytest.mark.parametrize("drift", ["extra_lf", "crlf"])
def test_offline_check_rejects_byte_drift_without_rewriting_a_semantically_identical_file(tmp_path, directory, drift):
    for name in ("hotels", "hotel_reviews", "hotel_profiles"):
        shutil.copytree(DATA_ROOT / name, tmp_path / name)
    path = next((tmp_path / directory).glob("*.json"))
    original = path.read_bytes()
    changed = original + b"\n" if drift == "extra_lf" else original.replace(b"\n", b"\r\n")
    assert json.loads(original) == json.loads(changed)
    path.write_bytes(changed)
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts/build_hotel_profiles.py"), "--data-dir", str(tmp_path), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert path.read_bytes() == changed
