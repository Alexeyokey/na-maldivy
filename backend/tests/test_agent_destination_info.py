from datetime import date

import pytest

from app.agent.brief import TripBrief
from app.agent.destination_info import destination_information, is_destination_information_question
from app.config import Settings
from app.tools.registry import create_tools

TODAY = date(2026, 10, 28)


def tools():
    return create_tools(Settings(_env_file=None, tools_mode="real", flights_mode="synthetic", llm_provider="fake"))


def test_cxr_trip_after_stored_rule_expiry_is_not_promised_and_prices_stay_unknown():
    brief = TripBrief(destination="CXR", year=2029, month=3, nights=9)
    information = destination_information(brief, tools(), today=TODAY)
    assert information is not None
    facts = information.facts
    assert facts["visa"]["effective_until"] == "2028-03-14"
    assert facts["visa_period_valid"] is False
    assert facts["trip_start"] == "2029-03-01" and facts["trip_end"] == "2029-04-09"
    assert facts["requested_period_kind"] == "whole_month_departure_window"
    assert facts["full_trip_price_rub"] is None and not facts["live_verified"]
    assert facts["prices"]["round_trip_flight_rub"]["MOW"] is None
    assert facts["source_urls"][0].startswith("https://vnembassy-moscow.mofa.gov.vn/")
    assert "гарантировать его нельзя" in information.text
    assert "Полная стоимость неизвестна" in information.text
    snapshot = date.fromisoformat(facts["as_of"])
    assert snapshot >= date.fromisoformat(facts["visa"]["checked_on"])
    assert snapshot.strftime("%d.%m.%Y") in information.text


def test_month_that_overlaps_expiry_is_not_treated_as_one_convenient_day():
    information = destination_information(
        TripBrief(destination="CXR", year=2028, month=3, nights=7), tools(), today=TODAY
    )
    assert information.facts["visa_period_valid"] is False
    assert information.facts["trip_end"] == "2028-04-07"


def test_undated_trip_does_not_invent_a_departure_or_establish_visa_validity():
    information = destination_information(TripBrief(destination="CXR", month=3), tools(), today=TODAY)
    assert information.facts["visa_period_valid"] is None
    assert information.facts["trip_start"] is None and information.facts["trip_end"] is None
    assert "Дат недостаточно" in information.text


def test_stay_limit_violation_is_separate_from_effective_period():
    information = destination_information(
        TripBrief(destination="CXR", start_date=date(2027, 3, 1), nights=50), tools(), today=TODAY
    )
    assert information.facts["visa_period_valid"] is True
    assert information.facts["stay_limit_valid"] is False
    assert "превышает" in information.text


def test_missing_tool_or_unknown_destination_returns_no_invented_fact():
    assert destination_information(TripBrief(destination="CXR"), {}, today=TODAY) is None
    assert destination_information(TripBrief(destination="ZZZ"), tools(), today=TODAY) is None


@pytest.mark.parametrize("message", ["Без визы в марте 2029, точно можно?", "Нужна ли виза?", "И сколько всего стоит?"])
def test_recognises_general_fact_questions(message):
    assert is_destination_information_question(message)


@pytest.mark.parametrize(
    "message", ["Хочу море без визовой возни в ноябре", "Во сколько будем в отеле?", "Найди отель"]
)
def test_ordinary_search_and_timeline_are_not_intercepted(message):
    assert not is_destination_information_question(message)
