"""Repeatable backend/HTTP rehearsal using recorded fares and simulated checkout.

This provides backend evidence only. It cannot mark an integrated UI rehearsal or
the full D9 acceptance complete. No .env, credentials, LLM calls or network.
"""

import argparse
import base64
import hashlib
import json
import platform
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.contracts.hotel_profiles import HotelReviewProfile
from app.main import create_app
from app.u2_eval import parse_sse

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 28)
MAIN_REQUEST = "Из Москвы в Анталью 18 ноября 2026 на неделю вдвоём"
SCOPE = "backend_http_through_simulated_payment_voucher_ics"
SEAT_PRICES = {("SVO", "AYT"): 11251, ("AYT", "SVO"): 14147}
DEMO_TRAVELERS = [
    {"full_name": "SYNTHETIC DEMO Traveler One", "birth_date": "1990-01-01"},
    {"full_name": "SYNTHETIC DEMO Traveler Two", "birth_date": "1991-01-01"},
]


@contextmanager
def forbid_external_network() -> Iterator[dict[str, int]]:
    attempts = {"blocked_attempts": 0}

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        attempts["blocked_attempts"] += 1
        raise AssertionError("External network is forbidden in the offline backend rehearsal")

    async def forbidden_async(*args: Any, **kwargs: Any) -> Any:
        return forbidden()

    with (
        patch.object(httpx.AsyncClient, "send", forbidden_async),
        patch.object(socket.socket, "connect", forbidden),
        patch.object(socket, "create_connection", forbidden),
    ):
        yield attempts


def replay_settings(cache_dir: Path, db_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        tools_mode="real",
        flights_mode="aviasales",
        trains_mode="synthetic",
        yandex_rasp_api_key=None,
        yandex_cache_dir=None,
        cache_mode="replay",
        llm_provider="fake",
        aviasales_api_token=None,
        aviasales_cache_dir=cache_dir,
        checkout_db_path=db_path,
        llm_api_key=None,
        llm_base_url=None,
        llm_model_main=None,
        llm_model_fast=None,
        data_dir=ROOT / "data",
        log_level="WARNING",
    )


def cache_evidence(cache_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Independently inspect D4 bodies; a public source label alone is insufficient."""
    rows, files = [], []
    if not cache_dir.is_dir():
        raise ValueError("Portable Aviasales replay cache is missing")
    for path in sorted(cache_dir.glob("*.json")):
        entry = json.loads(path.read_text())
        if not isinstance(entry, dict) or not {"request", "response", "recorded_at"} <= entry.keys():
            continue
        response = entry["response"]
        body = base64.b64decode(response["body_base64"], validate=True)
        if hashlib.sha256(body).hexdigest() != response["body_sha256"]:
            raise ValueError("D4 response body hash mismatch")
        if entry["request"]["headers"]:
            raise ValueError("Portable public cache must not contain request headers")
        data = json.loads(body)
        if response["status_code"] != 200 or data.get("success") is not True:
            raise ValueError("D4 response is not a successful provider response")
        for item in data.get("data", []):
            rows.append({**item, "recorded_at": entry["recorded_at"], "cache_file": path.name})
        files.append(
            {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "body_sha256": response["body_sha256"],
                "recorded_at": entry["recorded_at"],
            }
        )
    if not rows:
        raise ValueError("Portable Aviasales replay cache has no provider quotes")
    return rows, files


def _single(events: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    values = [row["data"] for row in events if row["type"] == kind]
    if len(values) != 1:
        raise AssertionError(f"Expected one {kind} event, found {len(values)}")
    return values[0]


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _public(value: Any, aliases: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _public(item, aliases) for key, item in value.items()}
    if isinstance(value, list):
        return [_public(item, aliases) for item in value]
    if isinstance(value, str):
        for private, public in aliases.items():
            value = value.replace(private, public)
    return value


def _match_quote(flight: dict[str, Any], raw_rows: list[dict[str, Any]]) -> dict[str, Any]:
    source = flight.get("price_source") or {}
    _require(source.get("provider") == "aviasales_data_api", "Card is not backed by Aviasales")
    _require(
        source.get("cache_mode") == "replay" and source.get("cache_hit") is True,
        "Flight card did not come from the replay cache",
    )
    expected = SEAT_PRICES[(flight["from"], flight["to"])]
    _require(source.get("seat_price_rub") == expected, "Unexpected recorded seat fare")
    matches = [
        row
        for row in raw_rows
        if (
            row.get("origin_airport") == flight["from"]
            and row.get("destination_airport") == flight["to"]
            and row.get("departure_at") == flight["departure_at"]
            and row.get("price") == expected
            and datetime.fromisoformat(row["recorded_at"]) == datetime.fromisoformat(source["recorded_at"])
        )
    ]
    _require(matches, "Agent card fare/date/source timestamp does not match a raw D4 response")
    _require(flight["price_rub"] == expected * 2, "Two-adult group fare is not two recorded seat prices")
    return {
        "from": flight["from"],
        "to": flight["to"],
        "departure_at": flight["departure_at"],
        "seat_price_rub": expected,
        "group_price_rub": flight["price_rub"],
        "cache_file": matches[0]["cache_file"],
        "recorded_at": source["recorded_at"],
    }


def validate_date_comparison(
    facts: dict[str, Any],
    events: list[dict[str, Any]],
    raw_rows: list[dict[str, Any]],
    selected: dict[str, Any],
) -> dict[str, Any]:
    """Accept absent adjacent data honestly; verify every positive claim when present."""
    quoted = [row for row in facts.get("checked_dates", []) if row["status"] == "quoted"]
    expected_status = "compared" if quoted else "unavailable"
    _require(facts.get("comparison_status") == expected_status, "D8 status does not match comparable quotes")
    _require(facts.get("state_changed") is False, "D8 reported a state change")
    _require(
        facts.get("baseline_option") == selected["index"]
        and facts.get("baseline_known_total_rub") == selected["known_total_rub"],
        "D8 changed its baseline option or total",
    )
    baseline = facts["baseline"]
    _require(facts["flight_source"] == "aviasales_data_api", "D8 switched the source to synthetic flights")
    _require(
        facts["price_scope"] == selected["price_scope"]
        and facts["unpriced_components"] == selected["unpriced_components"],
        "D8 changed price scope",
    )
    for row in quoted:
        quote = row["quote"]
        _require(
            quote["hotel_id"] == baseline["hotel_id"] == selected["hotel"]["hotel_id"]
            and quote["travelers"] == baseline["travelers"] == {"adults": 2, "children_ages": [], "pets": 0}
            and quote["nights"] == baseline["nights"] == 7,
            "D8 compared incompatible trips",
        )
        total = sum(quote[key] for key in ("flight_total_rub", "hotel_total_rub", "other_total_rub"))
        _require(
            row["known_total_rub"] == total and quote["departure_date"] == row["departure_date"],
            "D8 comparable quote components do not sum correctly",
        )
    by_date = {row["departure_date"]: row for row in quoted}
    for saving in facts.get("suggestions", []):
        _require(saving["suggested_departure"] in by_date, "D8 saving has no comparable quote")
        candidate = by_date[saving["suggested_departure"]]
        _require(
            saving["suggested_total_rub"] == candidate["known_total_rub"]
            and saving["original_total_rub"] == selected["known_total_rub"]
            and saving["saving_rub"] == saving["original_total_rub"] - saving["suggested_total_rub"]
            and saving["saving_rub"] * 100 > saving["original_total_rub"] * 5,
            "D8 saving amount or strict five-percent threshold is incorrect",
        )
    _require(bool(quoted) or not facts.get("suggestions"), "Absent date quotes produced invented savings")
    last_call = {}
    verified_sources = 0
    for event in events:
        data = event["data"]
        if event["type"] == "tool_call":
            last_call = data
        if event["type"] != "tool_result" or data.get("name") != "search_flights" or not data.get("ok"):
            continue
        _require(data.get("source") == "aviasales_data_api", "D8 flight result changed provider")
        args = last_call["arguments"]
        origin = "SVO" if args["origin"] == "MOW" else args["origin"]
        destination = "SVO" if args["destination"] == "MOW" else args["destination"]
        for source in data["quotes"]:
            _require(
                source["cache_mode"] == "replay" and source["cache_hit"] is True,
                "D8 positive result is not a replayed provider quote",
            )
            matches = [
                row
                for row in raw_rows
                if (
                    row.get("origin_airport") == origin
                    and row.get("destination_airport") == destination
                    and str(row.get("departure_at", ""))[:10] == args["departure_date"]
                    and row.get("price") == source["seat_price_rub"]
                    and datetime.fromisoformat(row["recorded_at"]) == datetime.fromisoformat(source["recorded_at"])
                )
            ]
            _require(matches, "D8 seat fare/date/timestamp is missing from raw D4 responses")
            verified_sources += 1
    _require(not quoted or verified_sources >= 2, "D8 comparable trip lacks raw provider evidence for both flight legs")
    return {
        "comparison_status": expected_status,
        "checked_dates": len(facts.get("checked_dates", [])),
        "comparable_dates": len(quoted),
        "suggestions": facts.get("suggestions", []),
        "provider_quotes_verified_against_raw_d4": verified_sources,
        "state_preserved": True,
    }


def run_one(index: int, output_dir: Path, cache_dir: Path) -> dict[str, Any]:
    started = time.perf_counter()
    stage, completed, trace, aliases, artifacts = "cache_provenance", [], [], {}, []
    result: dict[str, Any] = {"run": index, "scope": SCOPE, "status": "failed"}
    run_dir = output_dir / f"run-{index:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("voucher.html", "trip.ics", "trace.json"):
        (run_dir / filename).unlink(missing_ok=True)
    try:
        raw_rows, files = cache_evidence(cache_dir)
        result["cache_files"] = files
        completed.append(stage)
        with TemporaryDirectory(prefix="na-maldivy-rehearsal-private-") as private:
            settings = replay_settings(cache_dir, Path(private) / "checkout.sqlite3")
            app = create_app(settings)
            with forbid_external_network() as network, TestClient(app) as client:
                app.state.agent._today = lambda: TODAY
                session_id: str | None = None

                def chat(message: str) -> list[dict[str, Any]]:
                    nonlocal session_id
                    body = {"message": message}
                    if session_id is not None:
                        body["session_id"] = session_id
                    response = client.post("/api/chat", json=body)
                    _require(response.status_code == 200, "Chat HTTP status is not 200")
                    events = parse_sse(response.text)
                    _require(
                        events[-1]["type"] == "done" and not any(row["type"] == "error" for row in events),
                        "Chat returned an error or an incomplete stream",
                    )
                    session_id = _single(events, "done")["session_id"]
                    aliases[session_id] = "<demo-session>"
                    state_response = client.get(f"/api/session/{session_id}")
                    _require(state_response.status_code == 200, "Session snapshot unavailable")
                    trace.append({"stage": stage, "message": message, "events": events, "state": state_response.json()})
                    return events

                stage = "search_recorded_fares"
                events = chat(MAIN_REQUEST)
                options = _single(events, "options")
                _require(options["kind"] == "packages" and options["items"], "No actual trip cards found")
                safe = [row for row in options["items"] if row.get("can_confirm") is True]
                _require(safe, "No safe trip card can proceed to checkout")
                selected = safe[0]
                quotes = [_match_quote(selected[key], raw_rows) for key in ("outbound", "inbound")]
                result.update(
                    verified_quotes=quotes,
                    selected_option=selected["index"],
                    hotel_id=selected["hotel"]["hotel_id"],
                    known_total_rub=selected["known_total_rub"],
                    price_scope=selected["price_scope"],
                    unpriced_components=selected["unpriced_components"],
                )
                completed.append(stage)

                stage = "d7_profile_explanation"
                events = chat(f"Почему вариант {selected['index']} подходит семье? Что говорят отзывы?")
                profile = _single(events, "hotel_profile")
                _require(
                    profile.get("status") == "available" and profile.get("is_synthetic") is True,
                    "No transparent synthetic-review profile explanation",
                )
                _require(
                    profile.get("evidence") and profile["extraction"]["live_model_run"] is False,
                    "Missing evidence or inaccurate model-run claim",
                )
                typed_profile = HotelReviewProfile.model_validate(selected["hotel"]["review_profile"])
                _require(
                    profile["hotel_id"] == typed_profile.hotel_id and profile["segment"] == "family",
                    "Profile explanation used another hotel or guest segment",
                )
                guest = typed_profile.guest_profiles["family"]
                _require(
                    profile["segment_review_count"] == guest.review_count,
                    "Guest-segment count differs from the checked D7 profile",
                )
                for row in profile["counts"]:
                    expected = guest.aspects[row["aspect"]]
                    _require(
                        row["positive_count"] == expected.positive_count
                        and row["negative_count"] == expected.negative_count,
                        "D7 positive/negative counts changed in the explanation",
                    )
                corpus_name = typed_profile.hotel_id.replace(":", "-").replace("/", "-") + ".json"
                corpus_path = ROOT / "data/hotel_reviews" / corpus_name
                corpus = json.loads(corpus_path.read_text())
                reviews = {row["review_id"]: row for row in corpus["reviews"]}
                _require(
                    all(
                        row["review_id"] in reviews and row["quote"] in reviews[row["review_id"]]["text"]
                        for row in profile["evidence"]
                    ),
                    "D7 evidence quote is not literal in its source review",
                )
                result["d7_evidence"] = {
                    "hotel_id": typed_profile.hotel_id,
                    "segment": "family",
                    "profile_sha256": hashlib.sha256(
                        json.dumps(typed_profile.model_dump(), sort_keys=True).encode()
                    ).hexdigest(),
                    "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
                    "literal_quotes_verified": True,
                    "live_model_run": False,
                }
                completed.append(stage)

                stage = "d8_date_comparison"
                before = client.get(f"/api/session/{session_id}").json()
                events = chat(f"Для варианта {selected['index']}: если сдвинуть даты на 1–3 дня, можно сэкономить?")
                savings = _single(events, "date_savings")
                result["d8_comparison"] = validate_date_comparison(savings, events, raw_rows, selected)
                _require(
                    client.get(f"/api/session/{session_id}").json() == before,
                    "Read-only date comparison changed the selected trip",
                )
                completed.append(stage)

                stage = "select_safe_option"
                events = chat(f"Беру вариант {selected['index']}")
                _require(_single(events, "done")["stage"] == "awaiting_confirmation", "Selection not confirmed")
                completed.append(stage)

                stage = "confirm_cart_without_payment"
                events = chat("Подтверждаю")
                cart = _single(events, "booking")
                _require(
                    cart.get("status") == "awaiting_travelers" and cart.get("paid") is False,
                    "Confirmation must create an unpaid cart",
                )
                cart_id = cart["cart_id"]
                aliases[cart_id] = "<demo-cart>"
                cart_path = f"/api/checkout/carts/{cart_id}"
                completed.append(stage)

                stage = "synthetic_traveler_data"
                response = client.put(
                    cart_path + "/travelers", json={"session_id": session_id, "travelers": DEMO_TRAVELERS}
                )
                _require(
                    response.status_code == 200 and response.json()["status"] == "ready_for_demo_payment",
                    "Synthetic travelers did not match the group",
                )
                completed.append(stage)
                payment = {
                    "session_id": session_id,
                    "outcome": "declined",
                    "idempotency_key": "demo-decline-001",
                    "acknowledge_demo": True,
                    "acknowledge_unpriced": True,
                }

                stage = "deliberate_payment_decline"
                declined = client.post(cart_path + "/pay", json=payment)
                _require(
                    declined.status_code == 200
                    and declined.json()["status"] == "payment_declined"
                    and "order_id" not in declined.json(),
                    "Test decline incorrectly created an order",
                )
                trace.append({"stage": stage, "result": declined.json()})
                completed.append(stage)

                stage = "simulated_payment_approved"
                payment.update(outcome="approved", idempotency_key="demo-approval-001")
                approved = client.post(cart_path + "/pay", json=payment)
                _require(
                    approved.status_code == 200 and approved.json()["status"] == "paid_demo",
                    "Simulated approval did not produce a durable demo order",
                )
                order = approved.json()
                aliases[order["order_id"]] = "DEMO-REHEARSAL-ORDER"
                _require(
                    order.get("is_simulated") is True and order["package"] == selected,
                    "Paid demo order changed the quoted server snapshot",
                )
                trace.append({"stage": stage, "result": order})
                completed.append(stage)

                stage = "idempotent_payment_retry"
                repeated = client.post(cart_path + "/pay", json=payment)
                _require(
                    repeated.status_code == 200 and repeated.json() == order,
                    "Payment retry changed the order or produced a duplicate",
                )
                completed.append(stage)

                stage = "persisted_order_after_app_restart"
                with TestClient(create_app(settings)) as restarted:
                    response = restarted.get(
                        f"/api/checkout/orders/{order['order_id']}", headers={"X-Session-ID": session_id}
                    )
                    _require(
                        response.status_code == 200 and response.json() == order,
                        "Order did not survive a fresh app/session store",
                    )
                completed.append(stage)

                stage = "download_demo_voucher_and_calendar"
                for key, filename in (("voucher_path", "voucher.html"), ("calendar_path", "trip.ics")):
                    response = client.get(order[key], headers={"X-Session-ID": session_id})
                    _require(
                        response.status_code == 200 and response.headers.get("cache-control") == "no-store",
                        "Private artifact download failed",
                    )
                    _require(
                        "ДЕМО" in response.text and "Content-Disposition" in response.headers,
                        "Downloaded artifact is not explicitly a downloadable demo",
                    )
                    path = run_dir / filename
                    path.write_text(_public(response.text, aliases))
                    artifacts.append(
                        {
                            "path": str(path.relative_to(output_dir)),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        }
                    )
                calendar = (run_dir / "trip.ics").read_text().replace("\n ", "")
                _require(
                    calendar.count("BEGIN:VEVENT") == 3 and "DTSTART:" in calendar and "Z\n" in calendar,
                    "Calendar missing timezone-aware flight events",
                )
                completed.append(stage)
                _require(network["blocked_attempts"] == 0, "A stage attempted external network")
                result["external_network_attempts"] = network["blocked_attempts"]
            result["private_database_deleted"] = True  # TemporaryDirectory exits immediately below.
        _require(not Path(private).exists(), "Private rehearsal database was not cleaned up")
        result["status"] = "passed"
    except Exception as exc:
        result["failed_stage"] = stage
        result["failure"] = _public(f"{type(exc).__name__}: {exc}", aliases)
    duration = time.perf_counter() - started
    result.update(
        duration_seconds=round(duration, 3),
        completed_stages=completed,
        within_300_seconds=duration <= 300,
        artifacts=artifacts,
    )
    if duration > 300:
        result["status"] = "failed"
        result["failed_stage"] = "time_limit"
        result["failure"] = "Backend rehearsal exceeded 300 seconds"
    trace_path = run_dir / "trace.json"
    _write(trace_path, _public(trace, aliases))
    result["trace"] = {
        "path": str(trace_path.relative_to(output_dir)),
        "sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
    }
    return result


def _source_hashes() -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "backend/app").rglob("*.py"))
    }


def run_rehearsals(output_dir: Path, *, count: int = 5, cache_dir: Path | None = None) -> dict[str, Any]:
    if not 1 <= count <= 20:
        raise ValueError("Rehearsal count must be between 1 and 20")
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = cache_dir or ROOT / "data/demo/aviasales"
    started = datetime.now(UTC).isoformat()
    sources_before = _source_hashes()
    runs = [run_one(index, output_dir, cache_dir) for index in range(1, count + 1)]
    sources = _source_hashes()
    sources_unchanged = sources == sources_before
    cache_snapshots = {json.dumps(run.get("cache_files"), sort_keys=True) for run in runs}
    same_cache = len(cache_snapshots) == 1 and all(run.get("cache_files") for run in runs)
    passed = (
        all(run["status"] == "passed" and run["within_300_seconds"] for run in runs)
        and sources_unchanged
        and same_cache
    )
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    report = {
        "schema_version": 1,
        "started_at": started,
        "scope": SCOPE,
        "status": "passed" if passed else "failed",
        "runs_requested": count,
        "runs_passed": sum(run["status"] == "passed" for run in runs),
        "five_consecutive_backend_runs_passed": passed and count >= 5,
        "sources_unchanged_during_runs": sources_unchanged,
        "same_cache_snapshot_across_runs": bool(same_cache),
        "integrated_ui_rehearsal_status": "not_run",
        "full_d9_acceptance_status": "not_completed_by_this_report",
        "notice": "Backend/HTTP only. Simulated payment, no issued tickets, no real bookings. "
        "Integrated UI rehearsal and full D9 acceptance are not verified.",
        "provenance": {
            "python": platform.python_version(),
            "git_head": commit.stdout.strip() or None,
            "source_files_sha256": sources,
            "source_bundle_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
            "agent_evaluation_date": TODAY.isoformat(),
            "llm_provider": "fake",
            "live_model_run": False,
            "tools_mode": "real",
            "flights_mode": "aviasales",
            "cache_mode": "replay",
            "api_token_supplied": False,
            "env_file_loaded": False,
            "network_guard": ["httpx.AsyncClient.send", "socket.connect", "socket.create_connection"],
            "traveler_data": "synthetic demonstration fixtures",
            "private_database": "temporary and deleted",
        },
        "runs": runs,
    }
    _write(output_dir / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/backend-rehearsal")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data/demo/aviasales")
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()
    report = run_rehearsals(args.output_dir, count=args.count, cache_dir=args.cache_dir)
    print(
        json.dumps(
            {
                "status": report["status"],
                "runs_passed": report["runs_passed"],
                "runs_requested": report["runs_requested"],
                "scope": SCOPE,
                "integrated_ui_rehearsal_status": "not_run",
                "report": str(args.output_dir / "report.json"),
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
