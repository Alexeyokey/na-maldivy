"""Acceptance-runner safeguards: fail empty/mutated evidence and exercise HTTP."""

import json
import socket
from datetime import date

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import create_app
from app.u2_eval import evaluate_check, forbid_network, offline_settings, parse_sse, run_case, run_suite


def trace_for(*events, brief=None):
    return [{"state": {"brief": brief or {}}, "events": [{"type": kind, "data": data} for kind, data in events]}]


def test_empty_lists_do_not_pass_universal_or_zero_search_assertions():
    trace = trace_for(("options", {"kind": "packages", "items": []}))
    for key, expected in {
        "hotels.all.refundable": True,
        "options.all.total_price_rub.lte": 150000,
        "flights.all.timezone_aware": True,
        "options.count": 0,
    }.items():
        assert evaluate_check(key, expected, trace)["status"] == "failed"
    trace[0]["events"].append({"type": "tool_call", "data": {"name": "search_flights", "arguments": {}}})
    assert evaluate_check("options.count", 0, trace)["status"] == "passed"


def test_predicate_verifies_every_hotel_and_missing_evidence_is_not_success():
    trace = trace_for(
        (
            "options",
            {
                "kind": "packages",
                "items": [
                    {"hotel": {"refundable": True}, "outbound": {}, "inbound": {}},
                    {"hotel": {"refundable": None}, "outbound": {}, "inbound": {}},
                ],
            },
        )
    )
    assert evaluate_check("hotels.all.refundable", True, trace)["status"] == "failed"
    assert evaluate_check("response.nonexistent_property", True, trace)["status"] == "failed"


def savings_trace(*, suggested_flight=20000, shown=True, saving=10000):
    base = {
        "departure_date": "2026-11-18",
        "hotel_id": "osm:node/123",
        "travelers": {"adults": 2},
        "nights": 7,
        "flight_total_rub": 30000,
        "hotel_total_rub": 20000,
        "other_total_rub": 1000,
    }
    shifted = {**base, "departure_date": "2026-11-17", "flight_total_rub": suggested_flight}
    suggested_total = suggested_flight + 21000
    suggestion = {
        "original_total_rub": 51000,
        "suggested_departure": "2026-11-17",
        "suggested_total_rub": suggested_total,
        "saving_rub": saving,
    }
    return trace_for(
        (
            "date_savings",
            {
                "baseline": base,
                "baseline_known_total_rub": 51000,
                "price_scope": "round_trip_flights_baggage_hotel_arrival_transfer",
                "minimum_saving_percent": 5,
                "strict_threshold": True,
                "checked_dates": [
                    {
                        "departure_date": "2026-11-17",
                        "status": "quoted",
                        "quote": shifted,
                        "known_total_rub": suggested_total,
                    }
                ],
                "suggestions": [suggestion] if shown else [],
            },
        )
    )


def test_savings_arithmetic_and_threshold_cannot_be_faked_by_a_flag():
    key = "savings.amount_equals_complete_total_difference"
    assert evaluate_check(key, True, savings_trace())["status"] == "passed"
    assert evaluate_check(key, True, savings_trace(saving=9999))["status"] == "failed"
    key = "savings.only_above_threshold"
    assert evaluate_check(key, True, savings_trace(shown=False))["status"] == "failed"
    assert evaluate_check(key, True, savings_trace(suggested_flight=29999, saving=1))["status"] == "failed"
    assert evaluate_check(key, True, savings_trace(suggested_flight=29999, shown=False))["status"] == "passed"


def test_savings_component_labels_need_actual_round_trip_and_hotel_search_evidence():
    trace = savings_trace()
    for key in ("savings.comparison.includes_return_flight", "savings.comparison.includes_all_hotel_nights"):
        assert evaluate_check(key, True, trace)["status"] == "failed"


def test_sse_parser_rejects_unterminated_stream_and_handles_multiline_data():
    body = 'event: done\r\ndata: {"session_id":\r\ndata: "abc"}\r\n\r\n'
    assert parse_sse(body) == [{"type": "done", "data": {"session_id": "abc"}}]
    with pytest.raises(ValueError, match="terminate"):
        parse_sse('event: delta\ndata: {"text":"unfinished"}\n\n')


def test_network_guard_rejects_tcp_and_retains_in_process_http():
    with forbid_network(), TestClient(create_app(offline_settings())) as client:
        with pytest.raises(AssertionError, match="Network is forbidden"):
            socket.create_connection(("example.invalid", 443))
        assert client.get("/health").status_code == 200


async def test_network_guard_rejects_provider_http_before_transport():
    with forbid_network():
        async with httpx.AsyncClient() as client:
            with pytest.raises(AssertionError, match="Network is forbidden"):
                await client.get("https://example.invalid/")


def test_settings_ignore_shell_credentials_and_live_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "FAKE_EVAL_CANARY")
    monkeypatch.setenv("FLIGHTS_MODE", "aviasales")
    monkeypatch.setenv("AVIASALES_API_TOKEN", "FAKE_EVAL_CANARY")
    settings = offline_settings()
    assert settings.llm_provider == "fake" and settings.flights_mode == "synthetic"
    assert settings.llm_api_key is None and settings.aviasales_api_token is None


def test_actual_http_report_preserves_input_state_and_failed_properties(tmp_path):
    case = {
        "id": "sample",
        "text": "Питер Москва поездом 18 ноября 2026 купе на двоих",
        "tags": ["train"],
        "checks": {
            "brief.origin": "LED",
            "brief.destination": "MOW",
            "trains.all.seat_class": "coupe",
            "trains.all.timezone_aware": True,
            "brief.travelers.adults": 9,
        },
    }
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump([case], allow_unicode=True))
    report = run_suite(path, today=date(2026, 10, 28))
    assert report["summary"] == {"passed": 0, "failed": 1, "blocked": 0}
    result = report["cases"][0]
    assert result["original_text"] == case["text"]
    assert result["trace"][0]["state"]["brief"]["adults"] == 2
    checks = {item["property"]: item for item in result["checks"]}
    assert checks.pop("brief.travelers.adults")["status"] == "failed"
    assert all(check["status"] == "passed" for check in checks.values())
    assert any(event["type"] == "options" for event in result["trace"][0]["events"])
    assert report["network_forbidden"] and not report["env_file_loaded"]
    serialized = json.dumps(report)
    assert "FAKE_EVAL_CANARY" not in serialized
    assert report["provenance"]["source_and_data_sha256"]


def test_missing_selected_context_is_blocked_instead_of_inventing_a_hotel():
    case = {
        "id": "context",
        "text": "А если на день раньше?",
        "context": [
            "Хочу в Анталью",
            "Выбран отель; идентификатор берётся из результата поиска",
        ],
        "checks": {"savings.comparison.same_hotel_and_party": True},
        "tags": ["date_shift"],
    }
    with forbid_network(), TestClient(create_app(offline_settings())) as client:
        result = run_case(client, case)
    assert result["status"] == "blocked"
    assert len(result["trace"]) == 1
    assert result["checks"][0]["status"] == "blocked"
