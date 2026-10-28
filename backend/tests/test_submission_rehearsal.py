import base64
import copy
import hashlib
import json
import socket
from pathlib import Path

import pytest

from app.submission_rehearsal import (
    ROOT,
    SCOPE,
    cache_evidence,
    forbid_external_network,
    replay_settings,
    run_one,
    run_rehearsals,
    validate_date_comparison,
)


def test_missing_cache_fails_at_real_stage_and_never_claims_acceptance(tmp_path: Path) -> None:
    report = run_rehearsals(tmp_path / "report", count=5, cache_dir=tmp_path / "missing")
    assert report["status"] == "failed" and report["runs_passed"] == 0
    assert report["five_consecutive_backend_runs_passed"] is False
    assert all(row["failed_stage"] == "cache_provenance" and not row["completed_stages"] for row in report["runs"])
    assert report["integrated_ui_rehearsal_status"] == "not_run"
    assert report["full_d9_acceptance_status"] == "not_completed_by_this_report"


def test_offline_settings_override_shell_and_never_load_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "CANARY-DO-NOT-USE")
    monkeypatch.setenv("AVIASALES_API_TOKEN", "CANARY-DO-NOT-USE")
    monkeypatch.setenv("CACHE_MODE", "live")
    settings = replay_settings(tmp_path / "cache", tmp_path / "private.sqlite3")
    assert settings.llm_provider == "fake" and settings.cache_mode == "replay"
    assert settings.aviasales_api_token is None and settings.llm_api_key is None


def test_network_guard_rejects_tcp_connections() -> None:
    with forbid_external_network() as counts, pytest.raises(AssertionError, match="External network"):
        socket.create_connection(("127.0.0.1", 9))
    assert counts["blocked_attempts"] == 1


def test_cache_body_integrity_is_verified_independently(tmp_path: Path) -> None:
    raw = b'{"success":true,"data":[{"price":11251}]}'
    entry = {
        "recorded_at": "2026-10-28T07:54:10+00:00",
        "request": {"headers": []},
        "response": {
            "status_code": 200,
            "body_base64": base64.b64encode(raw).decode(),
            "body_sha256": hashlib.sha256(raw).hexdigest(),
        },
    }
    path = tmp_path / "entry.json"
    path.write_text(json.dumps(entry))
    assert cache_evidence(tmp_path)[0][0]["price"] == 11251
    entry["response"]["body_sha256"] = "0" * 64
    path.write_text(json.dumps(entry))
    with pytest.raises(ValueError, match="body hash mismatch"):
        cache_evidence(tmp_path)


def test_full_replay_backend_path_and_public_trace_privacy(tmp_path: Path) -> None:
    cache_dir = ROOT / "data/demo/aviasales"
    assert cache_dir.is_dir(), "The portable demo cache must be included with the submission"
    result = run_one(1, tmp_path, cache_dir)
    assert result["status"] == "passed", result
    assert result["scope"] == SCOPE and result["private_database_deleted"] is True
    assert {row["seat_price_rub"] for row in result["verified_quotes"]} == {11251, 14147}
    trace = (tmp_path / result["trace"]["path"]).read_text()
    assert "<demo-session>" in trace and "SYNTHETIC DEMO Traveler" in trace
    assert "X-Session-ID" not in trace and "CANARY-DO-NOT-USE" not in trace
    assert result["external_network_attempts"] == 0
    assert all(
        row["sha256"] == hashlib.sha256((tmp_path / row["path"]).read_bytes()).hexdigest()
        for row in result["artifacts"]
    )


@pytest.mark.parametrize("mutation", [None, "sum", "saving", "threshold", "source", "status"])
def test_date_comparison_validates_positive_numbers_and_raw_provider_evidence(mutation: str | None) -> None:
    scope = "round_trip_flights_baggage_hotel_arrival_transfer"
    extras = ["return_transfer", "visa_and_other_required_extras"]
    selected = {
        "index": 1,
        "known_total_rub": 100000,
        "price_scope": scope,
        "unpriced_components": extras,
        "hotel": {"hotel_id": "demo-hotel"},
    }
    trip = {
        "departure_date": "2026-11-19",
        "hotel_id": "demo-hotel",
        "nights": 7,
        "travelers": {"adults": 2, "children_ages": [], "pets": 0},
        "flight_total_rub": 70000,
        "hotel_total_rub": 19000,
        "other_total_rub": 1000,
    }
    facts = {
        "comparison_status": "compared",
        "state_changed": False,
        "baseline_option": 1,
        "baseline_known_total_rub": 100000,
        "baseline": copy.deepcopy(trip),
        "flight_source": "aviasales_data_api",
        "price_scope": scope,
        "unpriced_components": extras,
        "checked_dates": [
            {"status": "quoted", "departure_date": "2026-11-19", "quote": trip, "known_total_rub": 90000}
        ],
        "suggestions": [
            {
                "suggested_departure": "2026-11-19",
                "suggested_total_rub": 90000,
                "original_total_rub": 100000,
                "saving_rub": 10000,
            }
        ],
    }
    raw_rows, events = [], []
    for origin, destination, day, price in (("SVO", "AYT", "2026-11-19", 123), ("AYT", "SVO", "2026-11-26", 234)):
        raw_rows.append(
            {
                "origin_airport": origin,
                "destination_airport": destination,
                "departure_at": day + "T12:00:00+03:00",
                "price": price,
                "recorded_at": "2026-10-28T07:54:11+00:00",
            }
        )
        events.extend(
            [
                {
                    "type": "tool_call",
                    "data": {
                        "name": "search_flights",
                        "arguments": {"origin": origin, "destination": destination, "departure_date": day},
                    },
                },
                {
                    "type": "tool_result",
                    "data": {
                        "name": "search_flights",
                        "ok": True,
                        "source": "aviasales_data_api",
                        "quotes": [
                            {
                                "cache_mode": "replay",
                                "cache_hit": True,
                                "seat_price_rub": price,
                                "recorded_at": "2026-10-28T07:54:11Z",
                            }
                        ],
                    },
                },
            ]
        )
    if mutation == "sum":
        facts["checked_dates"][0]["known_total_rub"] += 1
    elif mutation == "saving":
        facts["suggestions"][0]["saving_rub"] += 1
    elif mutation == "threshold":
        facts["suggestions"][0]["saving_rub"] = 5000
    elif mutation == "source":
        raw_rows.clear()
    elif mutation == "status":
        facts["comparison_status"] = "unavailable"
    if mutation:
        with pytest.raises(AssertionError):
            validate_date_comparison(facts, events, raw_rows, selected)
    else:
        result = validate_date_comparison(facts, events, raw_rows, selected)
        assert result["comparison_status"] == "compared" and result["comparable_dates"] == 1
        assert result["provider_quotes_verified_against_raw_d4"] == 2
        facts.update(comparison_status="unavailable", checked_dates=[], suggestions=[])
        assert validate_date_comparison(facts, [], [], selected)["comparison_status"] == "unavailable"
