"""Бриф: проверка каждого поля отдельно, источники значений, пересборка."""

from datetime import date

from app.agent.brief import BriefPatch, TripBrief, apply_patch, merge_patches

TODAY = date(2026, 10, 27)


def test_bad_value_is_rejected_others_applied() -> None:
    result = apply_patch(TripBrief(), {}, BriefPatch(budget_rub=-5, month=11, origin="Питер"), today=TODAY)
    assert result.brief.month == 11
    assert result.brief.origin == "LED"  # нормализовано
    assert result.brief.budget_rub is None
    assert [r.field for r in result.rejected] == ["budget_rub"]


def test_unknown_destination_rejected_with_reason() -> None:
    result = apply_patch(TripBrief(), {}, BriefPatch(destination="Марс"), today=TODAY)
    assert result.brief.destination is None
    assert "нет в демо-каталоге" in result.rejected[0].reason


def test_past_date_rejected() -> None:
    result = apply_patch(TripBrief(), {}, BriefPatch(start_date="2026-10-01"), today=TODAY)
    assert result.brief.start_date is None
    assert result.rejected[0].reason == "дата уже прошла"


def test_sources_explicit_inferred_default() -> None:
    patch = BriefPatch(month=11, adults=2, inferred=["adults"], needs_add=["child"])
    result = apply_patch(TripBrief(), {}, patch, today=TODAY)
    assert result.sources == {"month": "explicit", "adults": "inferred", "needs": "inferred"}

    defaults = apply_patch(
        result.brief, result.sources, BriefPatch(nights=7), today=TODAY, default_fields=frozenset({"nights"})
    )
    assert defaults.sources["nights"] == "default"


def test_date_sets_month_and_new_month_drops_old_date() -> None:
    first = apply_patch(TripBrief(), {}, BriefPatch(start_date="2026-11-18"), today=TODAY)
    assert first.brief.month == 11 and first.sources["month"] == "inferred"

    second = apply_patch(first.brief, first.sources, BriefPatch(month=12), today=TODAY)
    assert second.brief.month == 12
    assert second.brief.start_date is None  # «а если в декабре?» — старая дата не действует


def test_cleared_field_returns_to_default() -> None:
    brief = TripBrief(budget_rub=100_000)
    result = apply_patch(brief, {"budget_rub": "explicit"}, BriefPatch(cleared=["budget_rub"]), today=TODAY)
    assert result.brief.budget_rub is None
    assert "budget_rub" not in result.sources


def test_needs_are_incremental() -> None:
    brief = TripBrief(needs=["sea", "child"])
    result = apply_patch(brief, {}, BriefPatch(needs_add=["pets"], needs_remove=["sea"]), today=TODAY)
    assert result.brief.needs == ["child", "pets"]


def test_search_changed_only_for_search_fields() -> None:
    assert not apply_patch(TripBrief(), {}, BriefPatch(mood="спокойно"), today=TODAY).search_changed
    assert apply_patch(TripBrief(), {}, BriefPatch(pets=1), today=TODAY).search_changed


def test_model_values_win_rules_fill_gaps() -> None:
    model = BriefPatch(month=12, needs_add=["sea"])
    rules = BriefPatch(month=11, origin="MOW", needs_add=["child"], inferred=["month"])
    merged = merge_patches(model, rules)
    assert merged.month == 12 and "month" not in merged.inferred
    assert merged.origin == "MOW"
    assert set(merged.needs_add) == {"sea", "child"}
