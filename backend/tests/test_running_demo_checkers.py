"""Run the standalone acceptance clients against actual keyless backend routes.

No Docker daemon, host socket, .env or external requests are needed here. The
frontend transport itself is exercised by its HTTP suite and the Docker smoke;
this adapter supplies its checked-in static entrypoint to the acceptance client.
"""

import importlib
import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.submission_rehearsal import forbid_external_network
from app.three_destination_rehearsal import mixed_demo_settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checkers(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("check_running_demo"), importlib.import_module("check_running_three_destinations")


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class GatewayAdapter:
    def __init__(self, client):
        self.client = client

    def open(self, request, **_kwargs):
        path = urlsplit(request.full_url).path
        if path == "/config.js":
            body = (
                b'window.NA_MALDIVY_CONFIG = Object.freeze({"mode":"api","apiEnabled":true,'
                b'"chatEndpoint":"/api/chat","healthEndpoint":"/health"});'
            )
            return Response(200, {"Content-Type": "text/javascript"}, body)
        if path in {"/", "/app.js", "/api-app.js"}:
            content = (ROOT / "frontend/dist" / ("index.html" if path == "/" else path[1:])).read_bytes()
            return Response(200, {"Content-Type": "text/html" if path == "/" else "text/javascript"}, content)
        response = self.client.request(
            request.get_method(), path, content=request.data, headers=dict(request.header_items())
        )
        return Response(response.status_code, response.headers, response.content)


@pytest.mark.parametrize("variant", ["three_reference", "future_demo", "three_future_demo"])
def test_acceptance_checks_real_seeded_routes_payment_changes_and_restart_without_keys(
    tmp_path, monkeypatch, checkers, variant
):
    demo, three = checkers
    for key in ("AVIASALES_API_TOKEN", "YANDEX_RASP_API_KEY", "LLM_API_KEY"):
        monkeypatch.setenv(key, "UNUSED-PRIVATE-CANARY")
    settings = mixed_demo_settings(ROOT / "data/demo/aviasales", tmp_path / "orders.sqlite3")
    checker_class = three.ThreeCheck if variant.startswith("three_") else demo.Check
    trip_date = date(2026, 11, 18) if variant == "three_reference" else date(2030, 3, 20)
    context = tmp_path / "private-context.json"
    check = checker_class("http://app:8080", context, False, trip_date)
    app = create_app(settings)
    with forbid_external_network() as network, TestClient(app) as client:
        app.state.agent._today = lambda: date(2026, 10, 29)
        check.opener = GatewayAdapter(client)
        check.initial()
        assert check.report["status"] == "passed"
        assert check.report["criteria_changes"] == {"paid_order_preserved": True, "train_directions_checked": 2}
        assert check.report["scenario"] == (
            "recorded_reference_fixture" if variant == "three_reference" else "future_demo"
        )
        assert network["blocked_attempts"] == 0
        private = json.loads(context.read_text())
        report = json.dumps(check.report)
        capabilities = private["orders"] if variant.startswith("three_") else [private]
        for row in capabilities:
            assert row["session_id"] not in report and row["order_id"] not in report
        assert "UNUSED-PRIVATE-CANARY" not in report and "1990-01-01" not in report
        assert context.stat().st_mode & 0o077 == 0

    # A fresh application has no old chat sessions but must recover identical
    # orders and downloadable documents from the same SQLite file.
    with forbid_external_network() as network, TestClient(create_app(settings)) as client:
        resumed = checker_class("http://app:8080", context, True, trip_date)
        resumed.opener = GatewayAdapter(client)
        resumed.resume()
        assert resumed.report["status"] == "passed"
        assert resumed.report["departure_date"] == trip_date.isoformat()
        assert network["blocked_attempts"] == 0


def test_fixture_date_expires_to_a_future_demo_without_pretending_to_replay_saved_fares(checkers):
    demo, _ = checkers
    assert demo.departure_date(today=date(2026, 10, 29)) == date(2026, 11, 18)
    assert demo.departure_date(today=date(2026, 11, 18)) == date(2026, 12, 16)
    assert demo.departure_date(today=date(2030, 1, 1)) == date(2030, 1, 29)
    with pytest.raises(ValueError, match="future"):
        demo.departure_date("2026-11-18", today=date(2026, 11, 18))


@pytest.mark.parametrize(
    "url",
    [
        "https://remote.invalid",
        "http://127.0.0.1:8080/?token=x",
        "http://app:8000",
        "http://owner:secret@localhost:8080",
    ],
)
def test_acceptance_client_rejects_remote_credentials_and_ambiguous_gateway_urls(checkers, url):
    demo, _ = checkers
    with pytest.raises(ValueError):
        demo.local_base_url(url)


def test_changed_text_and_train_events_cannot_hide_old_hotel_cards_in_the_snapshot(checkers, tmp_path):
    demo, _ = checkers
    check = demo.Check("http://localhost:8080", tmp_path / "unused", False, date(2026, 11, 18))
    check.chat = lambda _text: [
        {"type": "options", "data": {"kind": "trains", "items": [{"number": "DEMO-NIGHT"}]}},
        {"type": "done", "data": {"stage": "trains_shown"}},
    ]
    check.snapshot = lambda: {
        "stage": "packages_shown",
        "brief": {"destination": "MLE", "transport": "flight"},
        "selected": 1,
        "booking": {},
        "stale": False,
        "options": {"kind": "packages", "items": [{"hotel": {"name": "Old hotel"}}]},
    }
    with pytest.raises(demo.VerificationError, match="replace old cards"):
        check.verify_trip_changes({"order_id": "private-order"})


def test_redirects_cannot_forward_private_session_capabilities(checkers, tmp_path):
    demo, _ = checkers
    check = demo.Check("http://localhost:8080", tmp_path / "unused", False)
    handler = next(item for item in check.opener.handlers if isinstance(item, demo.urllib.request.HTTPRedirectHandler))
    assert handler.redirect_request(None, None, None, None, None, "https://outside.invalid") is None


@pytest.mark.parametrize(
    "mutation,expected_error",
    [
        ("repeated_other_destination", "server brief"),
        ("wrong_outbound_route", "wrong route"),
        ("wrong_return_route", "wrong route"),
        ("coherent_but_wrong_dates", "wrong departure date"),
        ("wrong_return_date", "wrong departure date"),
        ("wrong_stay", "seven-night stay"),
        ("wrong_package_group", "package route or group"),
        ("wrong_brief_group", "server brief"),
        ("stale_cards", "current unselected search"),
    ],
)
def test_future_demo_cannot_pass_with_a_different_trip_even_when_price_and_source_flags_are_valid(
    tmp_path, checkers, mutation, expected_error
):
    demo, _ = checkers
    trip_date = date(2030, 3, 20)
    settings = mixed_demo_settings(ROOT / "data/demo/aviasales", tmp_path / "orders.sqlite3")
    check = demo.Check("http://app:8080", tmp_path / "unused-context", False, trip_date)
    with forbid_external_network() as network, TestClient(create_app(settings)) as client:
        client.app.state.agent._today = lambda: date(2026, 10, 29)
        check.opener = GatewayAdapter(client)
        events = check.chat(f"Из Москвы в Дубай {trip_date.isoformat()} на неделю вдвоём")
        selected = deepcopy(next(row for row in demo.single(events, "options")["items"] if row["can_confirm"]))
        check.verify_package(selected, "DXB")  # The actual future route passes before mutation.
        snapshot = check.snapshot()
        if mutation == "repeated_other_destination":
            snapshot["brief"]["destination"] = selected["destination"] = "AYT"
            selected["outbound"]["to"] = selected["inbound"]["from"] = "AYT"
        elif mutation == "wrong_outbound_route":
            selected["outbound"]["to"] = "AYT"
        elif mutation == "wrong_return_route":
            selected["inbound"]["to"] = "LED"
        elif mutation == "coherent_but_wrong_dates":
            # Shift the entire internally consistent package. Unchanged prices,
            # provenance labels, checkout and D7/D8 would formerly accept it.
            for leg in (selected["outbound"], selected["inbound"]):
                for key in ("departure_at", "arrival_at"):
                    leg[key] = (datetime.fromisoformat(leg[key]) + timedelta(days=1)).isoformat()
            for key in ("check_in", "check_out"):
                selected["hotel"][key] = (date.fromisoformat(selected["hotel"][key]) + timedelta(days=1)).isoformat()
        elif mutation == "wrong_return_date":
            for key in ("departure_at", "arrival_at"):
                selected["inbound"][key] = (
                    datetime.fromisoformat(selected["inbound"][key]) + timedelta(days=1)
                ).isoformat()
        elif mutation == "wrong_stay":
            selected["hotel"]["check_out"] = (
                date.fromisoformat(selected["hotel"]["check_out"]) + timedelta(days=1)
            ).isoformat()
        elif mutation == "wrong_package_group":
            selected["travelers"]["adults"] = 1
        elif mutation == "wrong_brief_group":
            snapshot["brief"]["adults"] = 1
        elif mutation == "stale_cards":
            snapshot["stale"] = True
        snapshot["options"]["items"] = [selected]
        check.snapshot = lambda: snapshot
        with pytest.raises(demo.VerificationError, match=expected_error):
            check.verify_package(selected, "DXB")
        assert network["blocked_attempts"] == 0


@pytest.mark.parametrize("script", ["check_running_demo.py", "check_running_three_destinations.py"])
def test_cli_rejects_credential_url_without_echoing_it(script):
    import subprocess
    import sys

    secret = "PRIVATE-CLI-CANARY"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), "--base-url", f"http://owner:{secret}@localhost:8080"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert secret not in result.stdout + result.stderr
    assert "without credentials" in result.stderr
