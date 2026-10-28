"""Three destinations through HTTP: explicit provider snapshots plus labelled mocks.

This is an offline backend rehearsal, not UI/D9 acceptance or an external LLM run.
The strict Aviasales-only rehearsal remains a separate, unchanged check.
"""

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.contracts.hotel_profiles import HotelReviewProfile
from app.main import create_app
from app.submission_rehearsal import (
    DEMO_TRAVELERS,
    ROOT,
    TODAY,
    _public,
    _single,
    _source_hashes,
    _write,
    cache_evidence,
    forbid_external_network,
    replay_settings,
)
from app.u2_eval import parse_sse

SCOPE = "three_destination_mixed_demo_backend_http_through_simulated_checkout"
DESTINATIONS = {"AYT": "Анталью", "DXB": "Дубай", "MLE": "Мале"}
EXPECTED_SOURCES = {
    "AYT": ("provider_replay", "provider_replay"),
    "DXB": ("deterministic_mock", "provider_replay"),
    "MLE": ("provider_replay", "provider_replay"),
}


def _check(condition: Any, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _hash(value: Any) -> str:
    body = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(body).hexdigest()


def mixed_demo_settings(cache_dir: Path, db_path: Path) -> Settings:
    # Explicit replay_settings values override shell credentials and never load .env.
    return replay_settings(cache_dir, db_path).model_copy(update={"flights_mode": "demo"})


def verify_source(
    value: dict[str, Any],
    raw_rows: list[dict[str, Any]],
    *,
    origin: str,
    destination: str,
    departure_date: str,
    departure_at: str | None = None,
    group_price: int | None = None,
) -> dict[str, Any]:
    """Verify source flags independently, including raw D4 evidence for real legs."""
    demo = value.get("demo_source") or {}
    price = value.get("price_source")
    kind = demo.get("kind")
    _check(kind in {"provider_replay", "deterministic_mock"}, "Flight source must be explicit")
    if kind == "deterministic_mock":
        _check(price is None, "A mock must not carry an Aviasales price source")
        notice = demo.get("notice", "")
        _check(
            "ДЕМО-МОК" in notice and "не котировка Aviasales" in notice and "не наличие мест" in notice,
            "Mock notice must disclose generation and unavailable provider evidence",
        )
        _check(demo.get("reason") in {"cache_miss", "no_matching_quotes"}, "Mock needs an explicit fallback reason")
        return {"kind": kind, "reason": demo["reason"], "provider_evidence": False, "availability_confirmed": False}
    _check(
        isinstance(price, dict)
        and price.get("provider") == "aviasales_data_api"
        and price.get("cache_mode") == "replay"
        and price.get("cache_hit") is True,
        "Provider leg must identify a D4 replay hit",
    )
    _check("не подтвержден" in demo.get("notice", ""), "Provider replay cannot confirm availability")
    matches = [
        row
        for row in raw_rows
        if (
            row.get("origin_airport") == origin
            and row.get("destination_airport") == destination
            and str(row.get("departure_at", ""))[:10] == departure_date
            and row.get("price") == price["seat_price_rub"]
            and datetime.fromisoformat(row["recorded_at"]) == datetime.fromisoformat(price["recorded_at"])
            and (
                departure_at is None
                or datetime.fromisoformat(row["departure_at"]) == datetime.fromisoformat(departure_at)
            )
        )
    ]
    _check(matches, "Provider price, airport, date and recording timestamp must match raw D4")
    if group_price is not None:
        _check(group_price == price["seat_price_rub"] * 2, "Group fare must correspond to the two recorded seat fares")
    return {
        "kind": kind,
        "provider": "aviasales_data_api",
        "provider_evidence": True,
        "seat_price_rub": price["seat_price_rub"],
        "recorded_at": price["recorded_at"],
        "cache_file": matches[0]["cache_file"],
        "availability_confirmed": False,
    }


def _verify_flight(flight: dict[str, Any], raw_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return verify_source(
        flight,
        raw_rows,
        origin=flight["from"],
        destination=flight["to"],
        departure_date=flight["departure_at"][:10],
        departure_at=flight["departure_at"],
        group_price=flight["price_rub"],
    )


def verify_date_savings(
    facts: dict[str, Any],
    events: list[dict[str, Any]],
    raw_rows: list[dict[str, Any]],
    selected: dict[str, Any],
) -> dict[str, Any]:
    quoted = [row for row in facts.get("checked_dates", []) if row["status"] == "quoted"]
    _check(facts.get("comparison_status") == ("compared" if quoted else "unavailable"), "D8 status must match evidence")
    _check(
        facts.get("state_changed") is False and facts["baseline_option"] == selected["index"],
        "D8 cannot change the selected trip",
    )
    _check(
        facts["baseline_known_total_rub"] == selected["known_total_rub"]
        and facts["price_scope"] == selected["price_scope"]
        and facts["unpriced_components"] == selected["unpriced_components"],
        "D8 changed price scope or baseline",
    )
    _check(facts.get("demo_mode") is True, "Mixed rehearsal requires explicit D8 demo mode")
    baseline = facts["baseline"]
    known_sources = set()
    last_call: dict[str, Any] = {}
    verified_provider, verified_mock = 0, 0
    for event in events:
        data = event["data"]
        if event["type"] == "tool_call":
            last_call = data
        if event["type"] != "tool_result" or data.get("name") != "search_flights" or not data.get("ok"):
            continue
        _check(data.get("source") == "offline_demo", "D8 tool metadata must disclose the explicit demo adapter")
        args = last_call["arguments"]
        origin = "SVO" if args["origin"] == "MOW" else args["origin"]
        destination = "SVO" if args["destination"] == "MOW" else args["destination"]
        for flight in data["flights"]:
            proof = verify_source(
                flight, raw_rows, origin=origin, destination=destination, departure_date=args["departure_date"]
            )
            known_sources.add(_hash({"demo_source": flight["demo_source"], "price_source": flight["price_source"]}))
            verified_provider += proof["kind"] == "provider_replay"
            verified_mock += proof["kind"] == "deterministic_mock"
    by_date = {}
    for row in quoted:
        quote = row["quote"]
        _check(
            quote["hotel_id"] == baseline["hotel_id"] == selected["hotel"]["hotel_id"]
            and quote["nights"] == baseline["nights"] == 7
            and quote["travelers"] == baseline["travelers"] == {"adults": 2, "children_ages": [], "pets": 0},
            "D8 cannot compare a different physical hotel, stay or party",
        )
        _check(
            sum(quote[key] for key in ("flight_total_rub", "hotel_total_rub", "other_total_rub"))
            == row["known_total_rub"],
            "D8 component arithmetic disagrees",
        )
        kinds = []
        for leg in row["flight_sources"].values():
            _check(
                _hash({"demo_source": leg.get("demo_source"), "price_source": leg["price_source"]}) in known_sources,
                "D8 positive comparison source has no checked tool result",
            )
            kinds.append(leg["kind"])
        _check(row["contains_mock"] == ("deterministic_mock" in kinds), "D8 mock flag disagrees with its legs")
        by_date[row["departure_date"]] = row
    baseline_mock = any(
        leg["demo_source"]["kind"] == "deterministic_mock" for leg in (selected["outbound"], selected["inbound"])
    )
    contains_mock = baseline_mock or any(row.get("contains_mock", False) for row in quoted)
    _check(facts["contains_mock"] == contains_mock, "D8 overall mock flag is inaccurate")
    if contains_mock:
        _check(facts["savings_kind"] == "demonstration_estimate", "Mixed savings cannot be presented as provider-only")
    for saving in facts.get("suggestions", []):
        _check(saving["suggested_departure"] in by_date, "D8 saving needs a complete comparable quote")
        candidate = by_date[saving["suggested_departure"]]
        _check(
            saving["suggested_total_rub"] == candidate["known_total_rub"]
            and saving["original_total_rub"] == selected["known_total_rub"]
            and saving["saving_rub"] == saving["original_total_rub"] - saving["suggested_total_rub"]
            and saving["saving_rub"] * 100 > saving["original_total_rub"] * 5,
            "D8 saving or threshold is incorrect",
        )
        expected_mock = baseline_mock or candidate["contains_mock"]
        _check(saving["contains_mock"] == expected_mock, "D8 saving mock provenance is inaccurate")
        if expected_mock:
            _check(saving["savings_kind"] == "demonstration_estimate", "Mock-involved saving lacks demo label")
    return {
        "comparison_status": facts["comparison_status"],
        "comparable_dates": len(quoted),
        "suggestions": facts["suggestions"],
        "contains_mock": contains_mock,
        "provider_quote_checks": verified_provider,
        "mock_quote_checks": verified_mock,
        "state_preserved": True,
        "facts_sha256": _hash(facts),
    }


def run_destination(code: str, output_dir: Path, cache_dir: Path) -> dict[str, Any]:
    started = time.perf_counter()
    stage, completed, trace, aliases = "cache_provenance", [], [], {}
    result: dict[str, Any] = {"destination": code, "status": "failed", "scope": SCOPE}
    run_dir = output_dir / code.lower()
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in ("trace.json", "voucher.html", "trip.ics"):
        (run_dir / name).unlink(missing_ok=True)
    try:
        raw_rows, files = cache_evidence(cache_dir)
        result["cache_files"] = files
        completed.append(stage)
        with TemporaryDirectory(prefix="three-destination-private-") as private:
            settings = mixed_demo_settings(cache_dir, Path(private) / "orders.sqlite3")
            app = create_app(settings)
            with forbid_external_network() as network, TestClient(app) as client:
                app.state.agent._today = lambda: TODAY
                session_id = None

                def chat(message: str) -> list[dict[str, Any]]:
                    nonlocal session_id
                    body = {"message": message, **({"session_id": session_id} if session_id else {})}
                    response = client.post("/api/chat", json=body)
                    _check(response.status_code == 200, "Chat HTTP failed")
                    events = parse_sse(response.text)
                    _check(
                        events[-1]["type"] == "done" and not any(row["type"] == "error" for row in events),
                        "Chat SSE failed or incomplete",
                    )
                    session_id = _single(events, "done")["session_id"]
                    aliases[session_id] = "<demo-session>"
                    trace.append(
                        {
                            "stage": stage,
                            "message": message,
                            "events": events,
                            "state": client.get(f"/api/session/{session_id}").json(),
                        }
                    )
                    return events

                stage = "search_and_verify_every_leg"
                events = chat(f"Из Москвы в {DESTINATIONS[code]} 18 ноября 2026 на неделю вдвоём")
                options = _single(events, "options")
                _check(options["kind"] == "packages" and options["items"], "Search returned no trip cards")
                cards = options["items"]
                proofs = {
                    card["index"]: {leg: _verify_flight(card[leg], raw_rows) for leg in ("outbound", "inbound")}
                    for card in cards
                }
                safe = [
                    card
                    for card in cards
                    if card.get("can_confirm") is True
                    and not any(issue["severity"] == "critical" for issue in card["issues"])
                ]
                _check(safe, "No safe option exists; unsafe checkout must not be forced")
                selected = safe[0]
                leg_proofs = proofs[selected["index"]]
                _check(
                    tuple(leg_proofs[leg]["kind"] for leg in ("outbound", "inbound")) == EXPECTED_SOURCES[code],
                    "Primary route source composition differs from recorded coverage",
                )
                if code == "AYT":
                    _check(
                        [leg_proofs[leg]["seat_price_rub"] for leg in ("outbound", "inbound")] == [11251, 14147],
                        "Primary Antalya raw seat prices changed",
                    )
                answer = "".join(row["data"]["text"] for row in events if row["type"] == "delta")
                if any(proof["kind"] == "deterministic_mock" for proof in leg_proofs.values()):
                    _check("мок" in answer.casefold(), "Mixed trip explanation did not disclose mock flight")
                result.update(
                    leg_sources=leg_proofs,
                    hotel_name=selected["hotel"]["name"],
                    known_total_rub=selected["known_total_rub"],
                    price_scope=selected["price_scope"],
                    unpriced_components=selected["unpriced_components"],
                    traps=[
                        {
                            "option": card["index"],
                            "can_confirm": card["can_confirm"],
                            "rule": issue["rule"],
                            "severity": issue["severity"],
                            "message": issue["message"],
                        }
                        for card in cards
                        for issue in card["issues"]
                    ],
                    chosen_transfer_mode=selected["transfer"]["mode"],
                    arrival_is_night=(
                        datetime.fromisoformat(selected["outbound"]["arrival_at"]).hour < 6
                        or datetime.fromisoformat(selected["outbound"]["arrival_at"]).hour >= 22
                    ),
                    selected_option_safe=True,
                    shown_options=len(cards),
                )
                completed.append(stage)

                stage = "d7_checked_profile_and_literal_quotes"
                events = chat(f"Почему вариант {selected['index']} подходит семье? Что говорят отзывы?")
                facts = _single(events, "hotel_profile")
                profile = HotelReviewProfile.model_validate(selected["hotel"]["review_profile"])
                _check(
                    facts.get("status") == "available"
                    and facts["hotel_id"] == profile.hotel_id
                    and facts["is_synthetic"] is True
                    and facts["extraction"]["live_model_run"] is False,
                    "D7 must explain this hotel's synthetic baseline profile",
                )
                guest = profile.guest_profiles["family"]
                _check(facts["segment_review_count"] == guest.review_count, "D7 guest review count changed")
                for row in facts["counts"]:
                    actual = guest.aspects[row["aspect"]]
                    _check(
                        row["positive_count"] == actual.positive_count
                        and row["negative_count"] == actual.negative_count,
                        "D7 aspect counts changed",
                    )
                corpus_path = (
                    ROOT / "data/hotel_reviews" / (profile.hotel_id.replace(":", "-").replace("/", "-") + ".json")
                )
                corpus = json.loads(corpus_path.read_text())
                reviews = {row["review_id"]: row for row in corpus["reviews"]}
                _check(
                    facts["evidence"]
                    and all(row["quote"] in reviews[row["review_id"]]["text"] for row in facts["evidence"]),
                    "D7 quote is not literal in its review",
                )
                result["d7"] = {
                    "segment_review_count": guest.review_count,
                    "literal_quotes_verified": True,
                    "profile_sha256": _hash(profile.model_dump()),
                    "corpus_sha256": _hash(corpus_path.read_bytes()),
                    "is_synthetic": True,
                    "live_model_run": False,
                }
                completed.append(stage)

                stage = "d8_source_aware_comparison_preserves_state"
                before = client.get(f"/api/session/{session_id}").json()
                events = chat(f"Для варианта {selected['index']}: если сдвинуть даты на 1–3 дня, можно сэкономить?")
                facts = _single(events, "date_savings")
                result["d8"] = verify_date_savings(facts, events, raw_rows, selected)
                _check(client.get(f"/api/session/{session_id}").json() == before, "D8 mutated chat state")
                completed.append(stage)

                stage = "select_and_confirm_unpaid_cart"
                _check(
                    _single(chat(f"Беру вариант {selected['index']}"), "done")["stage"] == "awaiting_confirmation",
                    "Selected trip was not held for confirmation",
                )
                cart = _single(chat("Подтверждаю"), "booking")
                _check(cart["status"] == "awaiting_travelers" and cart["paid"] is False, "Confirmation must not pay")
                aliases[cart["cart_id"]] = "<demo-cart>"
                path = f"/api/checkout/carts/{cart['cart_id']}"
                completed.append(stage)

                stage = "synthetic_travelers_and_declined_payment"
                response = client.put(path + "/travelers", json={"session_id": session_id, "travelers": DEMO_TRAVELERS})
                _check(
                    response.status_code == 200 and response.json()["status"] == "ready_for_demo_payment",
                    "Synthetic travelers did not match the selected group",
                )
                payment = {
                    "session_id": session_id,
                    "outcome": "declined",
                    "idempotency_key": "three-dest-decline-001",
                    "acknowledge_demo": True,
                    "acknowledge_unpriced": True,
                }
                response = client.post(path + "/pay", json=payment)
                _check(
                    response.status_code == 200
                    and response.json()["status"] == "payment_declined"
                    and "order_id" not in response.json(),
                    "Decline must not create an order",
                )
                trace.append({"stage": stage, "result": response.json()})
                completed.append(stage)

                stage = "simulated_approval_and_idempotent_retry"
                payment.update(outcome="approved", idempotency_key="three-dest-approved-001")
                response = client.post(path + "/pay", json=payment)
                _check(
                    response.status_code == 200 and response.json()["status"] == "paid_demo",
                    "Simulated approval failed",
                )
                order = response.json()
                aliases[order["order_id"]] = "DEMO-THREEDEST-ORDER"
                _check(order["is_simulated"] is True and order["package"] == selected, "Paid snapshot changed")
                retry = client.post(path + "/pay", json=payment)
                _check(retry.status_code == 200 and retry.json() == order, "Payment retry changed the order")
                trace.append(
                    {
                        "stage": stage,
                        "status": "paid_demo",
                        "is_simulated": True,
                        "order_snapshot_sha256": _hash(order),
                        "travelers": "two fictitious demo adults",
                    }
                )
                completed.append(stage)

                stage = "voucher_calendar_and_restart_persistence"
                hashes = {}
                for key, filename in (("voucher_path", "voucher.html"), ("calendar_path", "trip.ics")):
                    response = client.get(order[key], headers={"X-Session-ID": session_id})
                    _check(
                        response.status_code == 200
                        and response.headers.get("cache-control") == "no-store"
                        and "attachment" in response.headers.get("content-disposition", ""),
                        "Private download failed",
                    )
                    text = response.text
                    _check("ДЕМО" in text, "Downloaded document must disclose simulation")
                    if filename == "voucher.html":
                        for title, leg in (("Туда", "outbound"), ("Обратно", "inbound")):
                            section = text.split(f"<li>{title}:", 1)[1].split("</li>", 1)[0]
                            if leg_proofs[leg]["kind"] == "deterministic_mock":
                                _check(
                                    "ДЕМО-МОК" in section
                                    and "не котировка Aviasales" in section
                                    and "Aviasales D4" not in section,
                                    "Voucher mislabels a mock as a provider quote",
                                )
                            else:
                                _check(
                                    "Aviasales D4" in section and "не подтверждены" in section,
                                    "Voucher provider leg lacks qualified source disclosure",
                                )
                        _check(
                            "билеты не выписаны" in text and "не забронированы" in text, "Voucher claimed real booking"
                        )
                    else:
                        unfolded = text.replace("\r\n ", "")
                        _check(unfolded.count("BEGIN:VEVENT") == 3, "Calendar must contain flights and hotel")
                        for leg in (selected["outbound"], selected["inbound"]):
                            for field, prefix in (("departure_at", "DTSTART:"), ("arrival_at", "DTEND:")):
                                instant = datetime.fromisoformat(leg[field]).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
                                _check(prefix + instant in unfolded, "Calendar lost a timezone-aware flight instant")
                    hashes[key] = _hash(response.content)
                    safe_text = text
                    for index, traveler in enumerate(DEMO_TRAVELERS, 1):
                        safe_text = safe_text.replace(traveler["full_name"], f"SYNTHETIC DEMO ADULT {index}")
                    (run_dir / filename).write_text(_public(safe_text, aliases))
                with TestClient(create_app(settings)) as restarted:
                    restored = restarted.get(
                        f"/api/checkout/orders/{order['order_id']}", headers={"X-Session-ID": session_id}
                    )
                    _check(restored.status_code == 200 and restored.json() == order, "Order lost after app restart")
                    _check(
                        restarted.get(f"/api/session/{session_id}").status_code == 404,
                        "Dialog state should be volatile",
                    )
                    for key in hashes:
                        replayed = restarted.get(order[key], headers={"X-Session-ID": session_id})
                        _check(
                            replayed.status_code == 200 and _hash(replayed.content) == hashes[key],
                            "Restart changed a download",
                        )
                result["download_hashes"] = hashes
                _check(network["blocked_attempts"] == 0, "Rehearsal attempted network")
                result["external_network_attempts"] = network["blocked_attempts"]
                completed.append(stage)
        _check(not Path(private).exists(), "Private order database was not removed")
        result.update(status="passed", private_database_deleted=True)
    except Exception as exc:
        result.update(status="failed", failed_stage=stage, error_type=type(exc).__name__)
        if isinstance(exc, AssertionError):
            result["failure"] = str(exc)  # All assertion messages above are static, never response contents.
    result.update(duration_seconds=round(time.perf_counter() - started, 3), completed_stages=completed)
    _write(run_dir / "trace.json", _public(trace, aliases))
    result["trace_sha256"] = _hash((run_dir / "trace.json").read_bytes())
    return result


def run_three_destinations(output_dir: Path, *, cache_dir: Path | None = None) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = cache_dir or ROOT / "data/demo/aviasales"
    started = datetime.now(UTC).isoformat()
    sources_before = _source_hashes()
    runs = [run_destination(code, output_dir, cache_dir) for code in DESTINATIONS]
    sources_after = _source_hashes()
    report = {
        "schema_version": 1,
        "started_at": started,
        "scope": SCOPE,
        "status": "passed" if all(row["status"] == "passed" for row in runs) else "failed",
        "destinations_passed": sum(row["status"] == "passed" for row in runs),
        "integrated_ui_status": "not_run",
        "full_d9_acceptance_status": "not_completed_by_this_report",
        "notice": "Offline backend only. Real saved search quotes and explicit mocks; no confirmed inventory, "
        "external LLM, charges, tickets or provider reservations.",
        "provenance": {
            "flights_mode": "demo",
            "tools_mode": "real",
            "cache_mode": "replay",
            "llm_provider": "fake",
            "live_model_run": False,
            "api_token_supplied": False,
            "env_file_loaded": False,
            "agent_evaluation_date": TODAY.isoformat(),
            "external_network_forbidden": True,
            "sources_unchanged": sources_before == sources_after,
            "source_bundle_sha256": _hash(sources_after),
            "sensitive_access_identifiers_removed_from_traces": True,
            "public_catalog_identifiers_retained_for_audit": True,
            "traveler_data": "two fictitious demo adults; no birth dates persisted in reports",
        },
        "runs": runs,
    }
    _write(output_dir / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "eval/reports/three-destination-backend-2026-10-28")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data/demo/aviasales")
    args = parser.parse_args()
    report = run_three_destinations(args.output_dir, cache_dir=args.cache_dir)
    print(
        json.dumps(
            {
                "status": report["status"],
                "destinations_passed": report["destinations_passed"],
                "integrated_ui_status": "not_run",
                "report": str(args.output_dir / "report.json"),
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
