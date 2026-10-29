"""Keep the user's actual permitted dates, independent of model shortcuts."""

import json
from datetime import date

import pytest
from pydantic import ValidationError

from app.agent.analysis import analyze_message
from app.agent.brief import SEARCH_FIELDS, BriefPatch, TripBrief, apply_patch, merge_patches
from app.agent.hidden import extract
from app.llm import FakeLLMClient, LLMResponse

TODAY = date(2026, 10, 29)
WINDOW = {
    "departure_window_start": "2026-10-30",
    "departure_window_end": "2027-04-29",
}


def test_exact_customer_request_retains_group_budget_and_open_window() -> None:
    patch = extract(
        "активный отдых у моря на двоих из Москвы до 200 тысяч, любые даты в ближайшие полгода. "
        "Нужны конкретные поездки с ценами",
        TODAY,
    )
    result = apply_patch(TripBrief(), {}, patch, today=TODAY)
    assert not result.rejected
    brief = result.brief
    assert brief.origin == "MOW" and brief.adults == 2 and brief.budget_rub == 200_000
    assert brief.has_flexible_dates and brief.start_date is None and brief.month is None
    assert brief.departure_window_start == date(2026, 10, 30)
    assert brief.departure_window_end == date(2027, 4, 29)
    assert {"active", "sea"} <= set(brief.needs)
    assert result.sources["departure_window_start"] == "explicit"
    assert result.search_changed
    assert set(WINDOW) | {"departure_months"} <= SEARCH_FIELDS


@pytest.mark.parametrize("text", ["Ноябрь декабрь январь февраль", "февраль, ноябрь, январь, декабрь"])
def test_all_stated_months_have_nearest_future_years(text: str) -> None:
    patch = extract(text, TODAY)
    assert patch.departure_months == ["2026-11", "2026-12", "2027-01", "2027-02"]
    assert patch.month is None and patch.start_date is None and patch.year is None


def test_generic_any_month_has_a_disclosed_bounded_assumption() -> None:
    patch = extract("Любой месяц, главное по цене чтобы оптимально вышло", TODAY)
    assert patch.departure_window_start == WINDOW["departure_window_start"]
    assert patch.departure_window_end == WINDOW["departure_window_end"]
    assert set(WINDOW) <= set(patch.inferred)


@pytest.mark.parametrize(
    ("today", "expected"),
    [(date(2026, 8, 31), "2027-02-28"), (date(2027, 8, 31), "2028-02-29")],
)
def test_half_year_means_calendar_months_and_clamps_short_months(today: date, expected: str) -> None:
    assert extract("любые даты в ближайшие полгода", today).departure_window_end == expected


def test_explicit_date_overrides_flexible_words_and_year_is_preserved() -> None:
    patch = extract("Любой месяц, но вылет 15 января 2027", TODAY)
    assert patch.start_date == "2027-01-15"
    assert patch.departure_window_start is None and patch.departure_months is None


def test_window_replaces_old_fixed_dates_and_sources_atomically() -> None:
    before = TripBrief(start_date="2026-12-15", nights=7)
    result = apply_patch(
        before,
        {"start_date": "explicit", "month": "inferred", "year": "inferred"},
        BriefPatch(**WINDOW),
        today=TODAY,
    )
    assert result.brief.has_flexible_dates
    assert result.brief.start_date is None and result.brief.month is None and result.brief.year is None
    assert set(result.sources) == set(WINDOW)


def test_single_exact_date_clears_old_window_and_month_list() -> None:
    before = TripBrief(**WINDOW, departure_months=["2026-11", "2026-12"])
    result = apply_patch(
        before, {name: "explicit" for name in WINDOW}, BriefPatch(start_date="2027-02-15"), today=TODAY
    )
    assert not result.rejected
    assert result.brief.start_date == date(2027, 2, 15)
    assert not result.brief.has_flexible_dates
    assert not set(WINDOW) & set(result.sources)


def test_selected_default_date_preserves_original_flexibility() -> None:
    before = TripBrief(**WINDOW, departure_months=["2026-11", "2026-12"])
    result = apply_patch(
        before,
        {name: "explicit" for name in WINDOW},
        BriefPatch(start_date="2026-11-15"),
        today=TODAY,
        default_fields=frozenset({"start_date", "month", "year"}),
    )
    assert not result.rejected and result.brief.has_flexible_dates
    assert result.brief.start_date == date(2026, 11, 15)
    assert result.brief.departure_months == before.departure_months
    assert result.brief.departure_window_end == before.departure_window_end
    assert result.sources["start_date"] == "default"


def test_default_date_outside_month_list_cannot_narrow_window() -> None:
    before = TripBrief(**WINDOW, departure_months=["2026-11"])
    result = apply_patch(
        before, {}, BriefPatch(start_date="2026-12-15"), today=TODAY, default_fields=frozenset({"start_date"})
    )
    assert result.brief == before
    assert [row.field for row in result.rejected] == ["start_date"]


@pytest.mark.parametrize(
    "fields",
    [
        {"departure_window_start": "2026-11-01"},
        {"departure_window_end": "2027-01-01"},
        {"departure_window_start": "2026-11-01", "departure_window_end": "2026-10-31"},
        {"departure_window_start": "2026-10-28", "departure_window_end": "2027-01-01"},
        {"departure_months": ["2026-09", "2027-01"]},
        {"departure_months": ["2027-13"]},
        {"departure_months": ["2027-1"]},
    ],
)
def test_invalid_flexible_dates_do_not_erase_existing_trip(fields: dict) -> None:
    before = TripBrief(start_date="2026-12-15", adults=1)
    result = apply_patch(before, {}, BriefPatch(**fields, adults=2), today=TODAY)
    assert result.rejected and result.brief.start_date == before.start_date
    assert result.brief.adults == 2 and not result.brief.has_flexible_dates


def test_invalid_single_month_does_not_erase_existing_window() -> None:
    before = TripBrief(**WINDOW)
    result = apply_patch(before, {}, BriefPatch(month=13), today=TODAY)
    assert result.rejected and result.brief == before


def test_month_list_replaces_old_window_and_is_sorted_deduplicated() -> None:
    before = TripBrief(**WINDOW)
    result = apply_patch(before, {}, BriefPatch(departure_months=["2027-02", "2026-11", "2027-02"]), today=TODAY)
    assert not result.rejected
    assert result.brief.departure_months == ["2026-11", "2027-02"]
    assert result.brief.departure_window_start is None and result.brief.departure_window_end is None


def test_malformed_range_is_rejected_when_constructing_a_brief() -> None:
    with pytest.raises(ValidationError, match="обе границы"):
        TripBrief(departure_window_start="2027-01-01")


def test_departure_membership_checks_inclusive_bounds_and_month_intersection() -> None:
    brief = TripBrief(**WINDOW, departure_months=["2026-11", "2027-04"])
    assert not brief.accepts_departure(date(2026, 10, 30))
    assert brief.accepts_departure(date(2026, 11, 1))
    assert not brief.accepts_departure(date(2027, 1, 1))
    assert brief.accepts_departure(date(2027, 4, 29))
    assert not brief.accepts_departure(date(2027, 4, 30))
    assert TripBrief(**WINDOW).accepts_departure(date(2026, 10, 30))
    assert TripBrief().accepts_departure(date(2026, 1, 1))


def test_primary_flexible_dates_remove_secondary_invented_fixed_month() -> None:
    merged = merge_patches(BriefPatch(**WINDOW), BriefPatch(month=1, year=2027, start_date="2027-01-15"))
    assert merged.month is None and merged.year is None and merged.start_date is None
    assert merged.departure_window_start == WINDOW["departure_window_start"]


def test_exact_new_date_removes_secondary_copied_range() -> None:
    merged = merge_patches(BriefPatch(start_date="2026-12-15"), BriefPatch(**WINDOW))
    assert merged.departure_window_start is None and merged.departure_window_end is None


@pytest.mark.parametrize("text", ["активный отдых на море: серфы, дайвинг", "активный отдых: сёрфинг и дайвинг"])
def test_activity_wishes_are_retained_without_inventing_services(text: str) -> None:
    patch = extract(text, TODAY)
    assert {"active", "surfing", "diving"} <= set(patch.needs_add)
    assert patch.min_stars is None and patch.direct_only is None


async def test_llm_cannot_replace_a_half_year_permission_with_a_january_question() -> None:
    llm = FakeLLMClient(
        [LLMResponse(text=json.dumps({"intent": "question", "patch": {"month": 1, "year": 2027}}), model="fake")]
    )
    result = await analyze_message(
        llm,
        "Любые даты в ближайшие полгода, предложи конкретные поездки с ценами",
        brief=TripBrief(origin="MOW", adults=2, budget_rub=200_000),
        history=[],
        today=TODAY,
        has_options=False,
        awaiting_confirmation=False,
    )
    assert result.source == "llm" and result.analysis.intent == "plan"
    assert result.analysis.patch.month is None and result.analysis.patch.year is None
    assert result.analysis.patch.departure_window_start == WINDOW["departure_window_start"]


async def test_llm_cannot_silently_select_january_from_four_months() -> None:
    llm = FakeLLMClient(
        [LLMResponse(text=json.dumps({"intent": "plan", "patch": {"month": 1, "year": 2027}}), model="fake")]
    )
    result = await analyze_message(
        llm,
        "Ноябрь декабрь январь февраль",
        brief=TripBrief(origin="MOW", adults=2, budget_rub=200_000),
        history=[],
        today=TODAY,
        has_options=False,
        awaiting_confirmation=False,
    )
    assert result.analysis.patch.departure_months == ["2026-11", "2026-12", "2027-01", "2027-02"]
    assert result.analysis.patch.month is None and result.analysis.patch.year is None
