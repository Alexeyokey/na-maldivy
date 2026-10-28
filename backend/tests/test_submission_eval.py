"""Protect proposed acceptance checks from vacuous success and product regressions."""

import json
from datetime import date

import pytest
import yaml

from app.agent.brief import TripBrief, apply_patch
from app.agent.hidden import extract
from app.submission_eval import check_property, observe, run_submission_suite
from app.u2_eval import ROOT

TODAY = date(2026, 10, 28)


def test_proposed_corpus_is_our_four_groups_of_ten_with_unique_original_requests():
    cases = []
    for zone in (1, 3, 4, 5):
        rows = yaml.safe_load((ROOT / f"eval/cases/u{zone}.yaml").read_text(encoding="utf-8"))
        assert len(rows) == 10
        assert all(row["proposal_status"] == "proposed/not_team_approved" for row in rows)
        assert all(len(row["checks"]) >= 3 for row in rows)
        cases.extend(rows)
    assert len({row["text"] for row in cases}) == 40
    assert len({row["id"] for row in cases}) == 40
    u2 = yaml.safe_load((ROOT / "eval/cases/u2.yaml").read_text(encoding="utf-8"))
    assert next(row for row in u2 if row["id"] == "u2-03")["checks"]["options.all.total_price_rub.lte"] == 150000


def test_empty_all_and_missing_fields_do_not_pass_as_unknown_or_false():
    empty = {"cards": {"packages": []}}
    assert check_property(empty, "cards.packages.all.total_rub.lte", 150000)["status"] == "failed"
    assert check_property(empty, "missing.field", None)["status"] == "failed"
    assert check_property(empty, "missing.field", False)["status"] == "failed"
    assert check_property({"known_unknown": None}, "known_unknown", None)["status"] == "passed"
    assert check_property(empty, "missing.field.present", False)["status"] == "passed"
    assert check_property({"cards": {"packages": [{}]}}, "cards.packages.all.total_rub", None)["status"] == "failed"


def test_every_list_item_is_checked_instead_of_only_the_first():
    observed = {"hotels": [{"refundable": True}, {"refundable": None}]}
    assert check_property(observed, "hotels.all.refundable", True)["status"] == "failed"
    observed["hotels"][1]["refundable"] = True
    assert check_property(observed, "hotels.all.refundable", True)["status"] == "passed"


def test_http_observation_checks_arithmetic_against_components_and_sse_terminal_event():
    package = {
        "known_total_rub": 333,
        "outbound": {"price_rub": 100, "baggage_price_rub": 10},
        "inbound": {"price_rub": 150, "baggage_price_rub": 10},
        "hotel": {"price_rub": 50, "check_in": "2026-11-18", "check_out": "2026-11-25", "nights": 7},
        "transfer": {"price_rub": 13},
    }
    turn = {
        "events": [{"type": "options", "data": {"kind": "packages", "items": [package]}}, {"type": "done", "data": {}}],
        "state": {"brief": {"nights": 7}, "version": 1},
        "session_id": "fixture-session",
    }
    assert observe([turn], {}, previous_session=None)["invariants"]["package_component_sum"] is True
    package["known_total_rub"] = 332
    assert observe([turn], {}, previous_session=None)["invariants"]["package_component_sum"] is False
    turn["events"][-1]["type"] = "error"
    assert observe([turn], {}, previous_session=None)["invariants"]["all_chat_turns_complete"] is False


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("бюджет теперь 1000 рублей", 1000),
        ("бюджет 1000", 1000),
        ("потрачу 1000р", 1000),
        ("Бюджет сейчас: 1000 ₽", 1000),
        ("бюджет 1 рубль", 1),
        ("потратим 250 рублей", 250),
        ("до 1000 рублей", 1000),
        ("за 1к", 1000),
        ("бюджет 150к", 150000),
        ("не дороже 150 000 ₽", 150000),
    ],
)
def test_explicit_money_context_supports_small_budgets(text, amount):
    assert extract(text, TODAY).budget_rub == amount


@pytest.mark.parametrize(
    "text",
    [
        "в ноябре 2026",
        "рейс SU 2026",
        "до 5 часов в пути",
        "до 5000 часов",
        "за 2026 год",
        "бюджет 2026 года пока не утверждён",
        "до 300 минут",
        "за 1000 мест",
        "ребёнку 5 лет",
    ],
)
def test_time_year_flight_number_and_counts_without_money_are_not_budgets(text):
    assert extract(text, TODAY).budget_rub is None


def test_negative_money_is_rejected_and_relative_budget_keeps_precedence():
    current = TripBrief(budget_rub=200000)
    invalid = apply_patch(current, {}, extract("бюджет -1000 рублей", TODAY), today=TODAY)
    assert invalid.brief.budget_rub == 200000 and invalid.rejected
    assert extract("бюджет меньше на 20 тысяч", TODAY, current).budget_rub == 180000


def test_real_http_corpus_keeps_our_proposal_separate_and_the_original_budget_failure(monkeypatch):
    # The runner explicitly overrides provider configuration without reading .env.
    monkeypatch.setenv("FLIGHTS_MODE", "aviasales")
    monkeypatch.setenv("TRAINS_MODE", "yandex")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("AVIASALES_API_TOKEN", "FAKE_SUBMISSION_EVAL_CANARY")
    report = run_submission_suite(today=TODAY)
    assert report["proposed_summary"] == {"passed": 40, "failed": 0, "blocked": 0}
    assert report["original_u2_summary"] == {"passed": 9, "failed": 1, "blocked": 0}
    assert report["summary"] == {"passed": 49, "failed": 1, "blocked": 0}
    assert report["approval_status"] == "proposed/not_team_approved"
    assert report["network_forbidden"] and not report["env_file_loaded"]
    assert {row["id"] for row in report["cases"] if row["status"] != "passed"} == {"u2-03"}
    assert len(report["corpus_files_sha256"]) == 5
    assert "FAKE_SUBMISSION_EVAL_CANARY" not in json.dumps(report)
    cases = {row["id"]: row for row in report["cases"]}
    assert cases["u4-03"]["fixture"] == "missing_transfer"
    assert cases["u4-08"]["fixture"] == "flight_source_error"
    assert cases["u5-08"]["observation"]["invariants"]["paid_retry_same_order"] is True
    assert cases["u5-09"]["api"]["voucher"]["status"] == 200
    assert cases["u5-10"]["api"]["calendar"]["status"] == 200
