import copy
import json
from pathlib import Path

import pytest

from app.agent.brief import TripBrief
from app.agent.review_explanations import explain_hotel_reviews
from app.agent.session import Session
from app.contracts.hotel_profiles import HotelReviewProfile

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def session() -> Session:
    paths = sorted((ROOT / "data/hotel_profiles").glob("*.json"))[:2]
    packages = []
    for index, path in enumerate(paths, 1):
        profile = HotelReviewProfile.model_validate_json(path.read_text())
        packages.append(
            {
                "index": index,
                "hotel": {
                    "hotel_id": profile.hotel_id,
                    "name": f"Demo hotel {index}",
                    "adults_only": None,
                    "pets_allowed": None,
                    "review_profile": profile.model_dump(mode="json"),
                },
            }
        )
    return Session(brief=TripBrief(adults=2, children_ages=[5]), packages=packages)


@pytest.mark.parametrize(
    "question,segment",
    [
        ("Почему этот отель подходит семье?", "family"),
        ("Что говорят отзывы для пары?", "couple"),
        ("Подойдёт ли отель с собакой?", "pets"),
        ("Отзывы для деловой поездки", "business"),
    ],
)
def test_segment_counts_and_literal_evidence_are_from_profile(session: Session, question: str, segment: str) -> None:
    before = copy.deepcopy(session)
    result = explain_hotel_reviews(session, question)
    assert result is not None and result.facts["segment"] == segment
    profile = HotelReviewProfile.model_validate(session.packages[0]["hotel"]["review_profile"])
    guest = profile.guest_profiles[segment]
    assert result.facts["profile_review_count"] == 24 and result.facts["segment_review_count"] == guest.review_count
    assert "Вариант не выбран" in result.text and "синтетические" in result.text
    assert "настоящий LLM-прогон не выполнялся" in result.text
    assert result.facts["extraction"]["live_model_run"] is False
    for row in result.facts["counts"]:
        expected = guest.aspects[row["aspect"]]
        assert row["positive_count"] == expected.positive_count
        assert row["negative_count"] == expected.negative_count
    assert len(result.facts["evidence"]) == 2
    assert {row["sentiment"] for row in result.facts["evidence"]} == {"positive", "negative"}
    for row in result.facts["evidence"]:
        assert row["quote"] in result.text and row["review_id"] in result.text
        assert any(
            e.quote == row["quote"] and e.review_id == row["review_id"] for e in guest.aspects[row["aspect"]].evidence
        )
    assert session == before  # explanations cannot alter selection, filters, brief or booking stage


def test_explicit_and_selected_options_do_not_mix_hotels(session: Session) -> None:
    session.selected = 2
    selected = explain_hotel_reviews(session, "Что говорят отзывы?")
    assert selected.facts["hotel_id"] == session.packages[1]["hotel"]["hotel_id"]
    assert selected.facts["selection_source"] == "selected"
    explicit = explain_hotel_reviews(session, "Расскажи отзывы про вариант 1")
    assert explicit.facts["hotel_id"] == session.packages[0]["hotel"]["hotel_id"]
    assert explicit.facts["selection_source"] == "explicit"
    second = explain_hotel_reviews(session, "Почему второй отель подходит паре?")
    assert second.facts["option"] == 2
    assert session.selected == 2


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("none", "missing_profile"),
        ("invalid", "invalid_or_mismatched_profile"),
        ("other_hotel", "invalid_or_mismatched_profile"),
        ("stale", "stale_options"),
    ],
)
def test_unknown_or_wrong_profile_never_fabricates_facts(session: Session, mutation: str, reason: str) -> None:
    if mutation == "none":
        session.packages[0]["hotel"]["review_profile"] = None
    elif mutation == "invalid":
        session.packages[0]["hotel"]["review_profile"]["review_count"] = 999
    elif mutation == "other_hotel":
        session.packages[0]["hotel"]["review_profile"] = session.packages[1]["hotel"]["review_profile"]
    elif mutation == "stale":
        session.stale = True
    result = explain_hotel_reviews(session, "Почему этот отель подходит семье?")
    assert result.facts["status"] == "unknown" and result.facts["reason"] == reason
    assert "evidence" not in result.facts and "counts" not in result.facts


def test_null_hard_rules_stay_unknown_despite_positive_fixture_quotes(session: Session) -> None:
    family = explain_hotel_reviews(session, "Почему этот отель подходит семье?")
    assert "принимает ли отель детей — неизвестно" in family.text
    pets = explain_hotel_reviews(session, "Что говорят отзывы с животными?")
    assert "проживание с животными неизвестно" in pets.text
    assert "live_model_run" not in family.text  # readable baseline notice instead


def test_unrelated_question_and_empty_options(session: Session) -> None:
    assert explain_hotel_reviews(session, "Сколько стоит перелёт?") is None
    empty = explain_hotel_reviews(Session(), "Что говорят отзывы?")
    assert empty.facts["status"] == "unknown"
    assert "Сначала найдите" in empty.text


def test_evidence_also_is_literal_in_underlying_synthetic_reviews(session: Session) -> None:
    result = explain_hotel_reviews(session, "Почему этот отель подходит семье?")
    hotel_id = result.facts["hotel_id"]
    corpus = json.loads(
        (ROOT / "data/hotel_reviews" / (hotel_id.replace(":", "-").replace("/", "-") + ".json")).read_text()
    )
    reviews = {row["review_id"]: row for row in corpus["reviews"]}
    assert all(row["quote"] in reviews[row["review_id"]]["text"] for row in result.facts["evidence"])
