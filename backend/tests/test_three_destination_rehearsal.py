import copy
import hashlib
from pathlib import Path

import pytest

from app.three_destination_rehearsal import (
    EXPECTED_SOURCES,
    ROOT,
    mixed_demo_settings,
    run_destination,
    run_three_destinations,
    verify_source,
)


@pytest.mark.parametrize("code", ["AYT", "DXB", "MLE"])
def test_three_actual_http_paths_safe_source_provenance_checkout_and_restart(tmp_path: Path, code: str) -> None:
    result = run_destination(code, tmp_path, ROOT / "data/demo/aviasales")
    assert result["status"] == "passed", result
    assert tuple(result["leg_sources"][leg]["kind"] for leg in ("outbound", "inbound")) == EXPECTED_SOURCES[code]
    assert result["selected_option_safe"] is True and result["private_database_deleted"] is True
    assert result["external_network_attempts"] == 0 and result["d7"]["live_model_run"] is False
    assert result["d8"]["state_preserved"] is True
    assert all(row["severity"] != "critical" or row["can_confirm"] is False for row in result["traps"])
    trace_path = tmp_path / code.lower() / "trace.json"
    trace = trace_path.read_text()
    assert "<demo-session>" in trace and "X-Session-ID" not in trace
    assert "1990-01-01" not in trace and "1991-01-01" not in trace
    assert result["trace_sha256"] == hashlib.sha256(trace_path.read_bytes()).hexdigest()
    voucher = (tmp_path / code.lower() / "voucher.html").read_text()
    assert "SYNTHETIC DEMO ADULT" in voucher and "DEMO-THREEDEST-ORDER" in voucher
    if code == "DXB":
        assert "ДЕМО-МОК" in voucher and "не котировка Aviasales" in voucher and "Aviasales D4" in voucher


def test_missing_cache_does_not_claim_three_successes_or_ui_acceptance(tmp_path: Path) -> None:
    report = run_three_destinations(tmp_path / "report", cache_dir=tmp_path / "missing")
    assert report["status"] == "failed" and report["destinations_passed"] == 0
    assert report["integrated_ui_status"] == "not_run"
    assert report["full_d9_acceptance_status"] == "not_completed_by_this_report"
    assert all(row["failed_stage"] == "cache_provenance" for row in report["runs"])


def test_mixed_settings_never_import_keys_or_live_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVIASALES_API_TOKEN", "CANARY-UNUSED-SECRET")
    monkeypatch.setenv("YANDEX_RASP_API_KEY", "CANARY-UNUSED-SECRET")
    monkeypatch.setenv("LLM_API_KEY", "CANARY-UNUSED-SECRET")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("CACHE_MODE", "live")
    settings = mixed_demo_settings(tmp_path / "cache", tmp_path / "orders.db")
    assert settings.flights_mode == "demo" and settings.cache_mode == "replay" and settings.llm_provider == "fake"
    assert (
        settings.aviasales_api_token is None and settings.yandex_rasp_api_key is None and settings.llm_api_key is None
    )


@pytest.mark.parametrize("mutation", ["price", "airport", "timestamp", "availability", "mock_with_provider"])
def test_source_provenance_cannot_be_relabelled_or_changed(mutation: str) -> None:
    value = {
        "demo_source": {"kind": "provider_replay", "notice": "Наличие не подтверждено"},
        "price_source": {
            "provider": "aviasales_data_api",
            "seat_price_rub": 11251,
            "cache_mode": "replay",
            "cache_hit": True,
            "recorded_at": "2026-10-28T07:54:11Z",
        },
    }
    raw = [
        {
            "origin_airport": "SVO",
            "destination_airport": "AYT",
            "departure_at": "2026-11-18T00:50:00+03:00",
            "price": 11251,
            "recorded_at": "2026-10-28T07:54:11+00:00",
            "cache_file": "public-fixture.json",
        }
    ]
    original = copy.deepcopy(value)
    assert verify_source(value, raw, origin="SVO", destination="AYT", departure_date="2026-11-18")["provider_evidence"]
    assert value == original
    if mutation == "price":
        value["price_source"]["seat_price_rub"] = 1
    elif mutation == "airport":
        raw[0]["destination_airport"] = "DWC"
    elif mutation == "timestamp":
        value["price_source"]["recorded_at"] = "2026-10-28T07:54:12Z"
    elif mutation == "availability":
        value["demo_source"]["notice"] = "Наличие гарантировано"
    elif mutation == "mock_with_provider":
        value["demo_source"] = {
            "kind": "deterministic_mock",
            "reason": "cache_miss",
            "notice": "ДЕМО-МОК: не котировка Aviasales и не наличие мест",
        }
    with pytest.raises(AssertionError):
        verify_source(value, raw, origin="SVO", destination="AYT", departure_date="2026-11-18")
