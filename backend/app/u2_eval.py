"""Offline acceptance runner for O2: real chat HTTP/SSE, rules and local data.

This is an observable-behaviour evaluation, not an LLM score or a provider test.
Assertions inspect HTTP state and emitted cards. Missing evidence fails explicitly;
empty result lists never satisfy ``all`` checks. Original inputs are retained.
"""

import argparse
import hashlib
import json
import platform
import re
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import yaml
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
ORIGIN_REPLY = "Из Москвы"


def parse_sse(body: str) -> list[dict[str, Any]]:
    """Parse SSE data lines without accepting a malformed or empty stream."""
    events = []
    for block in body.replace("\r\n", "\n").strip().split("\n\n"):
        name, lines = "message", []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                lines.append(line.removeprefix("data:").lstrip())
        if lines:
            events.append({"type": name, "data": json.loads("\n".join(lines))})
    if not events or events[-1]["type"] not in {"done", "error"}:
        raise ValueError("Chat stream did not terminate with done/error")
    return events


@contextmanager
def forbid_network() -> Iterator[None]:
    """Block outbound HTTP and TCP; TestClient's in-process ASGI stays usable."""

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Network is forbidden in the offline O2 evaluation")

    async def forbidden_async(*args: Any, **kwargs: Any) -> Any:
        return forbidden()

    with (
        patch.object(httpx.AsyncClient, "send", forbidden_async),
        patch.object(socket.socket, "connect", forbidden),
        patch.object(socket, "create_connection", forbidden),
    ):
        yield


def offline_settings() -> Settings:
    """Never load .env, keys, or provider configuration from the user's shell."""
    return Settings(
        _env_file=None,
        tools_mode="real",
        flights_mode="synthetic",
        trains_mode="synthetic",
        yandex_rasp_api_key=None,
        yandex_cache_dir=None,
        cache_mode="replay",
        llm_provider="fake",
        aviasales_api_token=None,
        aviasales_cache_dir=None,
        llm_api_key=None,
        llm_base_url=None,
        llm_model_main=None,
        llm_model_fast=None,
        data_dir=ROOT / "data",
        log_level="WARNING",
    )


def _events(trace: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [event["data"] for turn in trace for event in turn["events"] if event["type"] == kind]


def _items(trace: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    payloads = [item for item in _events(trace, "options") if item["kind"] == kind]
    return payloads[-1]["items"] if payloads else []


def _lookup(value: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _aware(value: Any) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _all(values: list[Any], predicate: Any) -> bool:
    return bool(values) and all(predicate(value) for value in values)


def _text(trace: list[dict[str, Any]]) -> str:
    return "\n".join(item["text"] for item in _events(trace, "delta")).lower().replace("ё", "е")


def evaluate_check(key: str, expected: Any, trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Interpret O2 semantic names using observable HTTP DTOs, without case IDs."""
    state = trace[-1]["state"] if trace else {}
    brief = state.get("brief", {})
    packages, destinations, trains = (_items(trace, kind) for kind in ("packages", "destinations", "trains"))
    hotels = [row["hotel"] for row in packages]
    flights = [row[leg] for row in packages for leg in ("outbound", "inbound")]
    text = _text(trace)
    calls = _events(trace, "tool_call")
    actual: Any = None
    reason = "Actual HTTP/SSE evidence does not satisfy the acceptance property"

    if key.startswith("brief."):
        path = key.removeprefix("brief.").removeprefix("travelers.")
        actual = (
            date.fromisoformat(brief["start_date"]).year
            if path == "year" and brief.get("start_date")
            else (_lookup(brief, path))
        )
    elif key == "options.count":
        # Zero must follow an actual search, not an unanswered clarification.
        actual = len(packages) if any(c["name"] == "search_flights" for c in calls) else None
    elif ".all." in key:
        collection, property_ = key.split(".all.", 1)
        rows = {
            "destinations": destinations,
            "hotels": hotels,
            "trains": trains,
            "options": packages,
            "flights": flights,
        }.get(collection, [])
        operation = next((op for op in ("gte", "lte", "in", "present") if property_.endswith("." + op)), None)
        path = property_.rsplit(".", 1)[0] if operation else property_
        path = {"total_price_rub": "total_rub", "total_price_scope": "party_price_scope"}.get(path, path)
        values = [_lookup(row, path) for row in rows]
        if path == "timezone_aware":
            values = [_aware(row.get("departure_at")) and _aware(row.get("arrival_at")) for row in rows]
        if collection == "trains" and path == "price_scope":
            # D1 price_rub is defined per group; insist on the same party in the actual call
            # and the public response's explicit one-way/group caveat.
            party = {
                "adults": brief.get("adults"),
                "children_ages": brief.get("children_ages"),
                "pets": brief.get("pets"),
            }
            group_call = any(c["name"] == "search_trains" and c["arguments"].get("travelers") == party for c in calls)
            values = ["group_one_way" if group_call and "за всю группу в одну сторону" in text else None for _ in rows]
        if path == "pet_transport_status.explicit":
            values = [bool(row.get("pet_transport_status")) for row in rows]
        if operation == "gte":
            actual = _all(values, lambda item: isinstance(item, (int, float)) and item >= expected)
        elif operation == "lte":
            actual = _all(values, lambda item: isinstance(item, (int, float)) and item <= expected)
        elif operation == "in":
            actual = _all(values, lambda item: item in expected)
        elif operation == "present":
            actual = _all(values, _present)
        else:
            actual = _all(values, lambda item: item == expected)
        reason = f"Nonempty {collection} required; observed {len(rows)} items, values={values!r}"
        expected = True
    elif key == "flights.selected.arrival_date_uses_destination_timezone":

        def local_arrival(row: dict[str, Any]) -> bool:
            value, zone = row.get("arrival_at"), row.get("destination_timezone")
            if not _aware(value) or not zone:
                return False
            instant = datetime.fromisoformat(value)
            return instant.utcoffset() == instant.astimezone(ZoneInfo(zone)).utcoffset()

        actual = _all(flights, local_arrival)
    elif key == "timeline.includes_transfer_and_checkin":
        required = {"arrival_transfer_departure", "arrival_transfer_arrival", "check_in"}
        actual = _all(
            packages, lambda row: required <= {item["kind"] for item in row.get("timeline", {}).get("events", [])}
        )
    elif key == "validation.early_arrival_vs_checkin.checked":
        actual = _all(
            packages,
            lambda row: (
                row.get("validation_status") == "checked"
                and (
                    "early_arrival_vs_checkin" in row.get("validation_checked_rules", [])
                    or "early_arrival_vs_checkin" in row.get("validation", {}).get("checked_rules", [])
                    or any(item.get("rule") == "early_arrival" for item in row.get("issues", []))
                )
            ),
        )
    elif key.startswith("savings."):
        actual, reason = _savings_check(key, _events(trace, "date_savings"), calls=calls)
    elif key == "response.no_fabricated_affordability":
        actual = not packages and bool(re.search(r"превышают бюджет|дороже бюджета|не подтвержден|не найден", text))
    elif key == "response.missing_or_over_budget_reason.present":
        actual = bool(re.search(r"превышают бюджет|нет предложений|источник.+недоступен|не подтвержден", text))
    elif key == "response.alternatives_or_clarification.present":
        actual = bool(
            _events(trace, "question") or re.search(r"увелич|помен|измен|друг.+дат|уточн|попроб|расшир", text)
        )
    elif key == "response.unknown_cancellation_deadline_explicit":
        actual = bool(re.search(r"срок[\w ]*(?:нужно|неизвест|уточн)|дедлайн[\w ]*неизвест", text))
    elif key == "response.cheapest_nonrefundable_excluded":
        actual = _all(hotels, lambda row: row.get("refundable") is True) and any(
            c["name"] == "search_hotels" and c["arguments"].get("refundable_only") is True for c in calls
        )
    elif key == "response.no_fabricated_availability":
        actual = (
            not packages
            and not _events(trace, "booking")
            and bool(re.search(r"не (?:могу |буду )?(?:выдум|придум|подтверд)|нет подтвержден|не подтвержден", text))
        )
    elif key == "response.synthetic_data_marked":
        actual = bool(re.search(r"синтет|демо|модельн", text))
    elif key == "response.unknown_price_not_zero":
        actual = (
            not re.search(r"(?:цена|стоимост)[\w ]*[:—-]?\s0\s*(?:₽|руб)", text)
            and not any(row.get("total_rub") == 0 for row in packages)
            and bool(re.search(r"неизвест|не подтвержден|не (?:могу |буду )?(?:выдум|придум|подтверд)", text))
        )
    elif key.startswith("response."):
        facts = _events(trace, "destination_info") or _events(trace, "destination_facts")
        fact = facts[-1] if facts else {}
        visa = fact.get("visa", {})
        if key == "response.visa_rule_expiry_checked":
            actual = bool(
                visa.get("effective_until")
                and fact.get("trip_end")
                and (date.fromisoformat(visa["effective_until"]) < date.fromisoformat(fact["trip_end"]))
                and fact.get("visa_period_valid") is False
            )
        elif key == "response.no_guarantee_beyond_known_visa_period":
            actual = fact.get("visa_period_valid") is False and bool(
                re.search(r"не.+(?:гарант|подтвержд)|перепровер|истек", text)
            )
        elif key == "response.missing_price_explicit":
            actual = bool(re.search(r"цен[аы] неизвест|стоимость неизвест|нет предложений|не.+подтвержден.+цен", text))
        elif key == "response.source_urls_and_as_of.present":
            actual = bool(fact.get("as_of") and fact.get("source_urls")) and all(
                str(url).startswith("https://") for url in fact.get("source_urls", [])
            )
    else:
        reason = "Unsupported acceptance property: add an explicit observable semantic mapping"
    passed = type(actual) is type(expected) and actual == expected
    return {
        "property": key,
        "expected": expected,
        "actual": actual,
        "status": "passed" if passed else "failed",
        "reason": "Observed property matched" if passed else reason,
    }


def _savings_check(key: str, facts: list[dict[str, Any]], *, calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if not facts:
        return False, "No structured date_savings event emitted for the selected physical hotel"
    fact = facts[-1]
    base = fact.get("baseline")
    checked = [item for item in fact.get("checked_dates", []) if item.get("status") == "quoted"]
    comparisons = [item.get("quote", {}) for item in checked]
    if not isinstance(base, dict) or not comparisons:
        return False, "Date savings need a base quote and nonempty checked comparisons"
    quotes = [base, *comparisons]

    def canonical(code: str | None) -> str | None:
        return "SVO" if code == "MOW" else code

    def matching_hotel_searches(quote: dict[str, Any]) -> list[dict[str, Any]]:
        searches = []
        for call in calls:
            args = call.get("arguments", {})
            if call.get("name") != "search_hotels" or args.get("travelers") != quote.get("travelers"):
                continue
            start, end = args.get("check_in"), args.get("check_out")
            if not start or not end or not quote.get("departure_date"):
                continue
            if (
                canonical(args.get("destination")) == canonical(quote.get("destination"))
                and (date.fromisoformat(end) - date.fromisoformat(start)).days == quote.get("nights")
                and 0 <= (date.fromisoformat(start) - date.fromisoformat(quote["departure_date"])).days <= 1
            ):
                searches.append(args)
        return searches

    def searched_both_legs(quote: dict[str, Any]) -> bool:
        flight_args = [
            item["arguments"]
            for item in calls
            if item.get("name") == "search_flights" and item["arguments"].get("travelers") == quote.get("travelers")
        ]
        origin, destination = canonical(quote.get("origin")), canonical(quote.get("destination"))
        out = any(
            canonical(item.get("origin")) == origin
            and canonical(item.get("destination")) == destination
            and item.get("departure_date") == quote.get("departure_date")
            for item in flight_args
        )
        return_days = {item["check_out"] for item in matching_hotel_searches(quote)}
        back = any(
            canonical(item.get("origin")) == destination
            and canonical(item.get("destination")) == origin
            and item.get("departure_date") in return_days
            for item in flight_args
        )
        return out and back

    if key == "savings.comparison.includes_return_flight":
        return (
            str(fact.get("price_scope", "")).startswith("round_trip_flights_baggage_hotel")
            and _all(quotes, lambda item: item.get("flight_total_rub", 0) > 0 and searched_both_legs(item)),
            "Every quote needs positive round-trip subtotal and actual outbound/return flight searches",
        )
    if key == "savings.comparison.includes_all_hotel_nights":
        return (
            isinstance(base.get("nights"), int)
            and base["nights"] > 0
            and _all(
                quotes,
                lambda item: (
                    item.get("nights") == base["nights"]
                    and item.get("hotel_total_rub", 0) > 0
                    and bool(matching_hotel_searches(item))
                ),
            ),
            "Not all hotel nights evidenced",
        )
    if key == "savings.comparison.same_hotel_and_party":
        return (
            bool(base.get("hotel_id"))
            and bool(base.get("travelers"))
            and _all(
                comparisons,
                lambda item: item.get("hotel_id") == base["hotel_id"] and item.get("travelers") == base["travelers"],
            ),
            "Physical hotel or party differ",
        )

    def total(quote: dict[str, Any]) -> int:
        return sum(quote.get(name, 0) for name in ("flight_total_rub", "hotel_total_rub", "other_total_rub"))

    suggestions = fact.get("suggestions", [])
    if key == "savings.amount_equals_complete_total_difference":
        by_day = {item.get("departure_date"): item for item in comparisons}
        valid = fact.get("baseline_known_total_rub") == total(base) and _all(
            checked, lambda item: item.get("known_total_rub") == total(item["quote"])
        )
        valid = valid and all(
            item.get("original_total_rub") == total(base)
            and item.get("suggested_departure") in by_day
            and item.get("suggested_total_rub") == total(by_day[item["suggested_departure"]])
            and item.get("saving_rub") == total(base) - item.get("suggested_total_rub", 0)
            for item in suggestions
        )
        return valid, "Savings must equal complete scoped total differences in independently summed quote components"
    if key == "savings.only_above_threshold":
        threshold = fact.get("minimum_saving_percent")
        eligible = {
            item["departure_date"] for item in comparisons if (total(base) - total(item)) * 100 > total(base) * 5
        }
        shown = {item.get("suggested_departure") for item in suggestions}
        return (
            threshold == 5
            and fact.get("strict_threshold") is True
            and shown == eligible
            and all(item.get("saving_rub", 0) * 100 > total(base) * threshold for item in suggestions),
            "Compare nonempty checked dates against the strict threshold; zero suggestions need zero qualifying quotes",
        )
    return False, "Unsupported savings property"


def _turn(client: TestClient, message: str, *, role: str, session_id: str | None) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.post("/api/chat", json={"message": message, "session_id": session_id})
    response.raise_for_status()
    if not response.headers.get("content-type", "").startswith("text/event-stream"):
        raise ValueError("Expected SSE content type")
    events = parse_sse(response.text)
    done = events[-1]
    sid = done["data"].get("session_id", session_id)
    state_response = client.get(f"/api/session/{sid}") if sid else None
    if state_response is not None:
        state_response.raise_for_status()
    return {
        "role": role,
        "message": message,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "http_status": response.status_code,
        "session_id": sid,
        "events": events,
        "state": state_response.json() if state_response is not None else {},
    }


def run_case(client: TestClient, case: dict[str, Any]) -> dict[str, Any]:
    started, trace, sid = time.perf_counter(), [], None
    blocked_reason = None
    try:
        for index, message in enumerate(case.get("context", [])):
            if index == 1 and "идентификатор берётся из результата поиска" in message:
                packages = _items(trace, "packages")
                if not packages:
                    blocked_reason = "Context search returned no physical hotel/package to select"
                    break
                selected = packages[0]
                message = f"Выбираю {selected['index']} вариант"
            turn = _turn(client, message, role="context", session_id=sid)
            trace.append(turn)
            sid = turn["session_id"]
        if blocked_reason is None:
            turn = _turn(client, case["text"], role="original", session_id=sid)
            trace.append(turn)
            sid = turn["session_id"]
            questions = _events([turn], "question")
            if questions and questions[-1]["field"] == "origin":
                # Only supply missing origin, never replace a failing original request.
                trace.append(_turn(client, ORIGIN_REPLY, role="clarification", session_id=sid))
        checks = [evaluate_check(key, value, trace) for key, value in case["checks"].items()]
        protocol = {
            "http.all_turns_complete": bool(trace)
            and all(turn["http_status"] == 200 and turn["events"][-1]["type"] == "done" for turn in trace),
            "http.no_error_events": not _events(trace, "error"),
            "http.no_unrequested_booking": not _events(trace, "booking"),
        }
        original_index = next((i for i, turn in enumerate(trace) if turn["role"] == "original"), None)
        if _events(trace, "date_savings") and original_index is not None and original_index > 0:
            before, after = trace[original_index - 1]["state"], trace[-1]["state"]
            protocol["http.date_comparison_preserves_brief_and_selection"] = (
                before["brief"] == after["brief"]
                and before.get("selected") == after.get("selected")
                and before.get("booking") == after.get("booking")
            )
        checks.extend(
            {
                "property": key,
                "expected": True,
                "actual": value,
                "status": "passed" if value else "failed",
                "reason": "Observed HTTP protocol invariant",
            }
            for key, value in protocol.items()
        )
        if blocked_reason is not None:
            for check in checks:
                check.update(status="blocked", reason=blocked_reason)
    except Exception as exc:
        blocked_reason = f"Runner/HTTP failure: {type(exc).__name__}"
        checks = [
            {"property": key, "expected": value, "actual": None, "status": "blocked", "reason": blocked_reason}
            for key, value in case["checks"].items()
        ]
    status = "blocked" if blocked_reason else "passed" if all(c["status"] == "passed" for c in checks) else "failed"
    return {
        "id": case["id"],
        "original_text": case["text"],
        "original_context": case.get("context", []),
        "tags": case["tags"],
        "status": status,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "checks": checks,
        "trace": trace,
        "blocked_reason": blocked_reason,
    }


def _manifest() -> dict[str, Any]:
    """Hash public source/data inputs; never traverse private .env or live caches."""
    files = sorted(
        [
            *[path for path in (ROOT / "backend/app").rglob("*.py")],
            *[
                path
                for path in (ROOT / "data").rglob("*")
                if path.is_file() and "cache" not in path.relative_to(ROOT / "data").parts
            ],
        ]
    )
    inputs = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    return {
        "git_head": revision.stdout.strip() or None,
        "source_and_data_sha256": hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
        "inputs": inputs,
    }


def run_suite(cases_path: Path, *, today: date) -> dict[str, Any]:
    cases = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("Expected a nonempty YAML case list")
    started = time.perf_counter()
    app = create_app(offline_settings())
    with forbid_network(), TestClient(app) as client:
        app.state.agent._today = lambda: today
        results = [run_case(client, case) for case in cases]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "evaluation_date": today.isoformat(),
        "scope": "offline_actual_http_sse_rules_local_data_no_external_llm_no_provider_network_no_booking",
        "notice": "Synthetic fares, schedules, policies and reviews; these are not confirmed supplier offers.",
        "settings": {"tools_mode": "real", "flights_mode": "synthetic", "cache_mode": "replay", "llm_provider": "fake"},
        "network_forbidden": True,
        "env_file_loaded": False,
        "python": platform.python_version(),
        "case_file": str(cases_path.relative_to(ROOT)) if cases_path.is_relative_to(ROOT) else cases_path.name,
        "case_file_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "provenance": _manifest(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "summary": {
            status: sum(row["status"] == status for row in results) for status in ("passed", "failed", "blocked")
        },
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "eval/cases/u2.yaml")
    parser.add_argument(
        "--today", type=date.fromisoformat, default=date.today(), help="Freeze the agent clock; recorded in report"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_suite(args.cases.resolve(), today=args.today)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "report": str(args.output)}, ensure_ascii=False))
    raise SystemExit(1 if report["summary"]["failed"] or report["summary"]["blocked"] else 0)


if __name__ == "__main__":
    main()
