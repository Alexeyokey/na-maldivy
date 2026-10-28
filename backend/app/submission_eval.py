"""Proposed offline acceptance corpus: actual HTTP, rules, synthetic data and fixtures.

The 40 new cases are our proposal, not the other participants' approved cases.
This measures observable backend invariants, never external-LLM quality or UI D9.
"""

import argparse
import copy
import hashlib
import json
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from app.main import create_app
from app.u2_eval import ROOT, _manifest, forbid_network, offline_settings, parse_sse
from app.u2_eval import run_case as run_u2_case

MISSING = object()


class FixtureTool:
    """Transform typed tool output, retaining real planner/router/SSE behaviour."""

    def __init__(self, tool: Any, fixture: str):
        self.delegate, self.fixture = tool, fixture
        for name in ("name", "description", "input_model", "output_adapter"):
            setattr(self, name, getattr(tool, name))

    async def run(self, input: Any) -> Any:
        if self.fixture == "flight_source_error" and self.name == "search_flights":
            raise RuntimeError("FABRICATED_OFFLINE_PROVIDER_FAILURE")
        if self.fixture == "missing_transfer" and self.name == "get_transfer":
            return []
        rows = await self.delegate.run(input)
        if self.fixture == "unknown_hotel_rules" and self.name == "search_hotels":
            return [
                row.model_copy(update={"refundable": None, "pets_allowed": None, "adults_only": None}) for row in rows
            ]
        if self.fixture == "unknown_destination_facts" and self.name == "suggest_destinations":
            unknown = dict.fromkeys(
                (
                    "water_temperature_c",
                    "air_temperature_c",
                    "flight_duration_minutes",
                    "estimated_total_price_rub",
                    "visa_required",
                    "visa",
                )
            )
            return [row.model_copy(update=unknown) for row in rows]
        return rows

    def public_metadata(self, result: Any) -> dict[str, Any]:
        metadata = self.delegate.public_metadata(result) if hasattr(self.delegate, "public_metadata") else {}
        return {**metadata, "offline_fixture": self.fixture}


@contextmanager
def fixture_tools(app: Any, fixture: str | None) -> Iterator[None]:
    original = app.state.agent._tools
    if fixture is None:
        yield
        return
    if fixture not in {"flight_source_error", "missing_transfer", "unknown_hotel_rules", "unknown_destination_facts"}:
        raise ValueError("Unknown offline fixture")
    app.state.agent._tools = {name: FixtureTool(tool, fixture) for name, tool in original.items()}
    try:
        yield
    finally:
        app.state.agent._tools = original


def _get(value: Any, path: str) -> Any:
    for part in path.split(".") if path else []:
        if part == "count" and isinstance(value, (list, dict, str)):
            value = len(value)
        elif isinstance(value, dict):
            if part not in value:
                return MISSING
            value = value[part]
        else:
            return MISSING
    return value


def _compare(actual: Any, expected: Any, operation: str | None) -> bool:
    if operation == "present":
        return (
            actual is not MISSING and actual is not None and actual != "" and actual != [] and actual != {}
        ) is expected
    if actual is MISSING:
        return False
    if operation in {"gte", "lte"}:
        return type(actual) in (int, float) and (actual >= expected if operation == "gte" else actual <= expected)
    if operation == "contains":
        values = expected if isinstance(expected, list) else [expected]
        return isinstance(actual, (str, list, dict)) and all(value in actual for value in values)
    if operation == "not_contains":
        values = expected if isinstance(expected, list) else [expected]
        return isinstance(actual, (str, list, dict)) and all(value not in actual for value in values)
    if operation == "in":
        return actual in expected
    return type(actual) is type(expected) and actual == expected


def check_property(observed: dict[str, Any], key: str, expected: Any) -> dict[str, Any]:
    """A small declarative path language; ``all`` always requires a nonempty list."""
    parts = key.split(".")
    operation = parts.pop() if parts[-1] in {"present", "gte", "lte", "contains", "not_contains", "in"} else None
    path = ".".join(parts)
    if ".all." in path:
        prefix, suffix = path.split(".all.", 1)
        rows = _get(observed, prefix)
        actual = [_get(row, suffix) for row in rows] if isinstance(rows, list) else None
        passed = bool(rows) and isinstance(rows, list) and all(_compare(value, expected, operation) for value in actual)
    else:
        actual = _get(observed, path)
        passed = _compare(actual, expected, operation)
    serialized = (
        ["MISSING_FIELD" if item is MISSING else item for item in actual]
        if isinstance(actual, list)
        else ("MISSING_FIELD" if actual is MISSING else actual)
    )
    return {"property": key, "expected": expected, "actual": serialized, "status": "passed" if passed else "failed"}


def _event_map(turns: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for turn in turns:
        for event in turn["events"]:
            result.setdefault(event["type"], []).append(event["data"])
    return result


def _cards(turns: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result = {"packages": [], "trains": [], "destinations": []}
    for option in _event_map(turns).get("options", []):
        result[option["kind"]] = option["items"]
    return result


def _aware(value: Any) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def observe(
    turns: list[dict[str, Any]], api: dict[str, Any], *, previous_session: dict[str, Any] | None
) -> dict[str, Any]:
    events, last_events = _event_map(turns), _event_map(turns[-1:])
    cards = _cards(turns)
    packages = cards["packages"]
    state = turns[-1]["state"] if turns else {}
    text = "\n".join(event["text"] for event in events.get("delta", []))
    last_text = "\n".join(event["text"] for event in last_events.get("delta", []))
    calls = events.get("tool_call", [])
    party = {key: state.get("brief", {}).get(key) for key in ("adults", "children_ages", "pets")}
    flights = [row[leg] for row in packages for leg in ("outbound", "inbound")]
    tool_names = [item["name"] for item in calls]
    last_tool_names = [item["name"] for item in last_events.get("tool_call", [])]
    versions = [turn["state"].get("version", 0) for turn in turns]
    totals = bool(packages) and all(
        row["known_total_rub"]
        == sum(row[leg]["price_rub"] + row[leg]["baggage_price_rub"] for leg in ("outbound", "inbound"))
        + row["hotel"]["price_rub"]
        + (row["transfer"]["price_rub"] if row.get("transfer") else 0)
        for row in packages
    )
    no_errors = all(turn["events"][-1]["type"] == "done" for turn in turns) and not events.get("error")
    matched_tools = all(
        [event["data"]["name"] for event in turn["events"] if event["type"] == "tool_call"]
        == [event["data"]["name"] for event in turn["events"] if event["type"] == "tool_result"]
        for turn in turns
    )
    final_turn_calls = last_events.get("tool_call", [])
    group_calls = [item for item in final_turn_calls if "travelers" in item["arguments"]]
    duplicate_payments = [
        value["body"] for name, value in api.items() if name.startswith("pay") and value["status"] == 200
    ]
    invariants = {
        "all_chat_turns_complete": bool(turns) and no_errors,
        "tool_calls_have_results": bool(calls) and matched_tools,
        "package_component_sum": totals,
        "flight_timestamps_aware": bool(flights)
        and all(_aware(row.get("departure_at")) and _aware(row.get("arrival_at")) for row in flights),
        "hotel_nights_match_dates": bool(packages)
        and all(
            (date.fromisoformat(row["hotel"]["check_out"]) - date.fromisoformat(row["hotel"]["check_in"])).days
            == row["hotel"]["nights"]
            == state["brief"]["nights"]
            for row in packages
        ),
        "final_search_party_matches_brief": bool(group_calls)
        and all(item["arguments"]["travelers"] == party for item in group_calls),
        "version_increased": len(versions) > 1 and versions[-1] > versions[0],
        "same_session": bool(turns) and len({turn["session_id"] for turn in turns}) == 1,
        "new_session_isolated": len(turns) > 1
        and turns[0]["session_id"] != turns[-1]["session_id"]
        and previous_session == turns[0]["state"],
        "paid_retry_same_order": len(duplicate_payments) >= 2
        and len({row.get("order_id") for row in duplicate_payments}) == 1
        and all(row.get("status") == "paid_demo" and row.get("order_id") for row in duplicate_payments),
    }
    return {
        "state": state,
        "first_state": turns[0]["state"] if turns else {},
        "previous_session": previous_session,
        "events": events,
        "last_events": last_events,
        "cards": cards,
        "text": text,
        "last_text": last_text,
        "tool_names": tool_names,
        "last_tool_names": last_tool_names,
        "api": api,
        "invariants": invariants,
    }


def _chat(client: TestClient, message: str, sid: str | None) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.post("/api/chat", json={"message": message, "session_id": sid})
    response.raise_for_status()
    events = parse_sse(response.text)
    sid = events[-1]["data"].get("session_id", sid)
    state = client.get(f"/api/session/{sid}")
    state.raise_for_status()
    return {
        "message": message,
        "http_status": response.status_code,
        "session_id": sid,
        "events": events,
        "state": state.json(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _variables(turns: list[dict[str, Any]], api: dict[str, Any]) -> dict[str, Any]:
    state = turns[-1]["state"]
    cards = _cards(turns)["packages"]
    option = state.get("selected") or next((row["index"] for row in cards if row.get("can_confirm") is True), 1)
    adults = state["brief"]["adults"]
    travelers = [
        {"full_name": f"Demo Fictional Adult {i + 1}", "birth_date": f"{1990 + i}-01-01"} for i in range(adults)
    ]
    departure = (
        date.fromisoformat(state["brief"]["start_date"]) if state["brief"].get("start_date") else date(2026, 11, 18)
    )
    for index, age in enumerate(state["brief"].get("children_ages", [])):
        born = departure.replace(year=departure.year - age, day=min(departure.day, 28))
        travelers.append({"full_name": f"Demo Fictional Child {index + 1}", "birth_date": born.isoformat()})
    result = {
        "session_id": state["session_id"],
        "version": state["version"],
        "stale_version": max(0, state["version"] - 1),
        "option": option,
        "demo_travelers": travelers,
        "api": api,
    }
    for value in api.values():
        if isinstance(value["body"], dict):
            for key in ("cart_id", "order_id"):
                if key in value["body"]:
                    result[key] = value["body"][key]
    return result


def _substitute(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        result = _get(variables, value[1:])
        if result is MISSING:
            raise ValueError("Unknown request variable")
        return copy.deepcopy(result)
    if isinstance(value, dict):
        return {key: _substitute(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, variables) for item in value]
    return value


def run_proposed_case(client: TestClient, app: Any, case: dict[str, Any]) -> dict[str, Any]:
    started, turns, api, previous = time.perf_counter(), [], {}, None
    try:
        with fixture_tools(app, case.get("fixture")):
            for message in [*case.get("context", []), case["text"], *case.get("followups", [])]:
                new_session = isinstance(message, dict) and message.get("new_session") is True
                content = message["message"] if isinstance(message, dict) else message
                sid = None if not turns or new_session else turns[-1]["session_id"]
                turns.append(_chat(client, content, sid))
                if new_session:
                    response = client.get(f"/api/session/{turns[0]['session_id']}")
                    response.raise_for_status()
                    previous = response.json()
            for index, action in enumerate(case.get("actions", [])):
                variables = _variables(turns, api)
                path = action["path"].format_map(variables)
                if not path.startswith("/api/") or "//" in path:
                    raise ValueError("Only local /api/ requests are allowed")
                body = _substitute(action.get("json"), variables)
                headers = _substitute(action.get("headers", {}), variables)
                response = client.request(action["method"], path, json=body, headers=headers)
                response_body = (
                    response.json()
                    if response.content and response.headers.get("content-type", "").startswith("application/json")
                    else None
                )
                api[action.get("as", f"request_{index}")] = {
                    "method": action["method"],
                    "path": path,
                    "status": response.status_code,
                    "body": response_body,
                    "text": response.text,
                    "cache_control": response.headers.get("cache-control"),
                    "content_type": response.headers.get("content-type"),
                }
            observed = observe(turns, api, previous_session=previous)
            # Checkout can advance the session after the last chat turn.
            session_response = client.get(f"/api/session/{turns[-1]['session_id']}")
            if session_response.status_code == 200:
                observed["state"] = session_response.json()
            checks = [check_property(observed, key, value) for key, value in case["checks"].items()]
            checks.append(check_property(observed, "invariants.all_chat_turns_complete", True))
            status = "passed" if all(item["status"] == "passed" for item in checks) else "failed"
            error_type = None
    except Exception as exc:
        checks, status, observed, error_type = [], "blocked", None, type(exc).__name__
    return {
        "id": case["id"],
        "corpus_role": "our_proposed_addition",
        "approval_status": "proposed/not_team_approved",
        "original_text": case["text"],
        "original_context": case.get("context", []),
        "original_followups": case.get("followups", []),
        "original_actions": case.get("actions", []),
        "tags": case["tags"],
        "fixture": case.get("fixture"),
        "status": status,
        "checks": checks,
        "turns": turns,
        "api": api,
        "observation": observed,
        "blocked_error_type": error_type,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def run_submission_suite(*, today: date, include_original: bool = True) -> dict[str, Any]:
    started = time.perf_counter()
    paths = [ROOT / f"eval/cases/u{zone}.yaml" for zone in (1, 3, 4, 5)]
    cases = [case for path in paths for case in yaml.safe_load(path.read_text(encoding="utf-8"))]
    with tempfile.TemporaryDirectory(prefix="submission-eval-") as directory:
        settings = offline_settings().model_copy(update={"checkout_db_path": Path(directory) / "private.sqlite3"})
        app = create_app(settings)
        with forbid_network(), TestClient(app) as client:
            app.state.agent._today = lambda: today
            results = [run_proposed_case(client, app, case) for case in cases]
            if include_original:
                originals = yaml.safe_load((ROOT / "eval/cases/u2.yaml").read_text(encoding="utf-8"))
                for case in originals:
                    result = run_u2_case(client, case)
                    result.update(corpus_role="original_u2", approval_status="original_draft_not_team_approved")
                    results.append(result)

    def summary(rows: list[dict[str, Any]]) -> dict[str, int]:
        return {status: sum(row["status"] == status for row in rows) for status in ("passed", "failed", "blocked")}

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "evaluation_date": today.isoformat(),
        "approval_status": "proposed/not_team_approved",
        "authorship": "Our proposed additions; not attributed to other project participants",
        "scope": "actual_backend_http_sse_rules_synthetic_data_explicit_failure_fixtures_no_external_llm_no_ui",
        "network_forbidden": True,
        "env_file_loaded": False,
        "personal_data": "Only fabricated Demo Fictional travelers; no real personal data",
        "notice": "Not a team-approved corpus, LLM quality metric, live provider test or full UI D9 acceptance.",
        "corpus_files_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [*paths, ROOT / "eval/cases/u2.yaml"]
        },
        "provenance": _manifest(),
        "summary": summary(results),
        "proposed_summary": summary([row for row in results if row["corpus_role"] == "our_proposed_addition"]),
        "original_u2_summary": summary([row for row in results if row["corpus_role"] == "original_u2"]),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    parser.add_argument("--proposed-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_submission_suite(today=args.today, include_original=not args.proposed_only)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "summary": report["summary"],
                "proposed": report["proposed_summary"],
                "original_u2": report["original_u2_summary"],
                "report": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if not report["summary"]["failed"] and not report["summary"]["blocked"] else 1)


if __name__ == "__main__":
    main()
