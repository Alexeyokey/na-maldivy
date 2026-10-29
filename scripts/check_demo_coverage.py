#!/usr/bin/env python3
"""Read-only data/planner coverage, without .env, credentials, HTTP or TCP.

Run with the backend Python environment. Only an explicitly requested report is
written; this does not exercise chat, a model, booking or a supplier's inventory.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import re
import socket
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from itertools import product
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agent.brief import TripBrief  # noqa: E402
from app.agent.planner import search_train_trip, search_trip  # noqa: E402
from app.config import Settings  # noqa: E402
from app.contracts.hotel_profiles import HotelReviewProfile  # noqa: E402
from app.tools.hotel_profiles import (  # noqa: E402
    SyntheticReview,
    build_profile,
    extract_lexical,
    make_synthetic_reviews,
)
from app.tools.registry import create_tools, run_tool  # noqa: E402

TODAY = date(2026, 10, 29)
REFERENCE = date(2026, 11, 18)
HOTEL_ID = re.compile(r"osm:(node|way|relation)/[1-9][0-9]*\Z")
CACHE_FILE = re.compile(r"[0-9a-f]{64}\.json\Z")


class NetworkBlocked(RuntimeError):
    """An offline check attempted an external connection."""


@contextmanager
def forbid_external_network():
    attempts = []

    def blocked(*args, **kwargs):
        attempts.append("blocked")
        raise NetworkBlocked("External network is disabled for demo coverage")

    async def blocked_async(*args, **kwargs):
        blocked()

    with (
        patch.object(httpx.Client, "send", blocked),
        patch.object(httpx.AsyncClient, "send", blocked_async),
        patch.object(socket.socket, "connect", blocked),
        patch.object(socket.socket, "connect_ex", blocked),
        patch.object(socket, "create_connection", blocked),
        patch.object(socket, "getaddrinfo", blocked),
    ):
        yield attempts


class OfflineSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # Do not even consult environment, dotenv or secret-file sources.
        return (init_settings,)


def offline_settings(data_dir: Path) -> Settings:
    return OfflineSettings(
        _env_file=None,
        log_level="WARNING",
        cors_origins="http://localhost:8080",
        tools_mode="real",
        data_dir=data_dir,
        flights_mode="demo",
        aviasales_api_token=None,
        aviasales_cache_dir=data_dir / "demo/aviasales",
        trains_mode="synthetic",
        yandex_rasp_api_key=None,
        yandex_cache_dir=None,
        checkout_db_path=None,
        llm_provider="fake",
        llm_base_url=None,
        llm_api_key=None,
        llm_model_main=None,
        llm_model_fast=None,
        llm_timeout_s=60,
        llm_max_retries=0,
        codex_binary="codex",
        codex_local_token=None,
        cache_mode="replay",
    )


def check_data(data_dir: Path) -> dict:
    try:
        return _check_data(data_dir)
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        return {
            "status": "failed",
            "counts": {"hotels": 0, "reviews": 0, "profiles": 0, "cache_entries": 0},
            "failures": ["malformed_data_shape"],
            "data_sha256": None,
        }


def _check_data(data_dir: Path) -> dict:
    """Check the bundled identities, literal profiles and cache without writing."""
    data_dir = data_dir.resolve()
    failures, fingerprints = [], {}
    counts = {"hotels": 0, "reviews": 0, "profiles": 0, "cache_entries": 0}

    def require(condition, code):
        if not condition and code not in failures:
            failures.append(code)
        return bool(condition)

    def read(name):
        try:
            path = data_dir / name
            if not path.resolve().is_relative_to(data_dir):
                raise ValueError("Data reference escapes the data directory")
            raw = path.read_bytes()
            fingerprints[name] = hashlib.sha256(raw).hexdigest()
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("Expected a JSON document")
            return value
        except (OSError, ValueError, TypeError):
            require(False, "unreadable_data")
            return {}

    def indexed(rows, code):
        if not isinstance(rows, list):
            require(False, code)
            return {}
        valid = [r for r in rows if isinstance(r, dict) and isinstance(r.get("hotel_id"), str)]
        result = {r["hotel_id"]: r for r in valid}
        require(
            len(result) == len(rows) and all(HOTEL_ID.fullmatch(key) for key in result),
            code,
        )
        return result

    catalog = indexed(read("hotels/catalog.json").get("hotels"), "catalog_ids")
    ids = set(catalog)
    counts["hotels"] = len(ids)
    require(len(ids) == 60, "catalog_size")
    require(
        Counter(row.get("destination") for row in catalog.values()) == {"AYT": 20, "DXB": 20, "MLE": 20},
        "destinations",
    )
    rules = indexed(read("hotels/synthetic_rules.json").get("hotels"), "rules_ids")
    transfers = indexed(read("transfers/transfers.json").get("transfers"), "transfer_ids")
    osm = indexed(read("hotels/osm_hotels.json").get("hotels"), "osm_ids")
    for records, code in (
        (rules, "rules_ids"),
        (transfers, "transfer_ids"),
        (osm, "osm_ids"),
    ):
        require(set(records) == ids, code)
    aliases = read("hotels/id_aliases.json").get("aliases")
    require(isinstance(aliases, dict) and set(aliases.values()) == ids, "alias_ids")

    expected_files = {key.replace(":", "-").replace("/", "-") + ".json" for key in ids}
    for directory in ("hotel_reviews", "hotel_profiles"):
        require(
            {p.name for p in (data_dir / directory).glob("*.json")} == expected_files,
            directory + "_ids",
        )
    for hotel_id in sorted(ids):
        filename = hotel_id.replace(":", "-").replace("/", "-") + ".json"
        corpus = read("hotel_reviews/" + filename)
        profile = read("hotel_profiles/" + filename)
        require(corpus.get("hotel_id") == hotel_id, "hotel_reviews_ids")
        require(profile.get("hotel_id") == hotel_id, "hotel_profiles_ids")
        raw_reviews = corpus.get("reviews")
        if not isinstance(raw_reviews, list):
            require(False, "review_count")
            continue
        counts["reviews"] += len(raw_reviews)
        counts["profiles"] += bool(profile)
        require(len(raw_reviews) == 24 and profile.get("review_count") == 24, "review_count")
        require(
            corpus.get("is_synthetic") is True and corpus.get("real_guest_reviews_used") is False,
            "review_provenance",
        )
        require(profile.get("is_synthetic") is True, "profile_provenance")
        try:
            reviews = [SyntheticReview.model_validate(row) for row in raw_reviews]
            require(
                len({row.review_id for row in reviews}) == len(reviews),
                "review_identity",
            )
            require(all(row.hotel_id == hotel_id for row in reviews), "review_identity")
            rule = rules[hotel_id]
            guests = profile.get("guest_profiles", {})
            aspects = profile.get("aspects", {})
            family = guests.get("family", {}).get("aspects", {}).get("children", {})
            pet_guest = guests.get("pets", {}).get("aspects", {}).get("pets", {})
            child_positive = [
                m for row in reviews for m in row.annotations if m.aspect == "children" and m.sentiment == "positive"
            ]
            pet_positive = [
                m for row in reviews for m in row.annotations if m.aspect == "pets" and m.sentiment == "positive"
            ]
            if rule.get("adults_only") is True:
                require(
                    not child_positive
                    and not family.get("positive_count")
                    and not aspects.get("children", {}).get("positive_count"),
                    "profile_children_policy",
                )
            if rule.get("pets_allowed") is False:
                require(
                    not pet_positive
                    and not pet_guest.get("positive_count")
                    and not aspects.get("pets", {}).get("positive_count"),
                    "profile_pets_policy",
                )
            checked_profile = HotelReviewProfile.model_validate(profile)
            require(
                reviews == make_synthetic_reviews(hotel_id, rule),
                "review_seed_reproduction",
            )
            extracted = extract_lexical(reviews)
            require(
                all(row.mentions == review.annotations for row, review in zip(extracted, reviews, strict=True)),
                "review_literal_evidence",
            )
            expected = build_profile(reviews, extracted)
            require(
                checked_profile.model_dump(mode="json") == expected,
                "profile_reproduction",
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            require(False, "profile_structure_or_evidence")
    require(counts["reviews"] == 1440 and counts["profiles"] == 60, "corpus_totals")

    cache = "demo/aviasales/"
    manifest = read(cache + "manifest.json")
    entries = manifest.get("entries")
    require(
        manifest.get("credential_required_for_replay") is False and manifest.get("is_live_availability") is False,
        "cache_provenance",
    )
    if isinstance(entries, list):
        counts["cache_entries"] = len(entries)
        names = [entry.get("file") for entry in entries if isinstance(entry, dict)]
        valid_names = {name for name in names if isinstance(name, str) and CACHE_FILE.fullmatch(name)}
        actual = {p.name for p in (data_dir / cache).glob("*.json") if p.name != "manifest.json"}
        require(
            len(entries) == len(names) == len(valid_names) == 47 and actual == valid_names,
            "cache_manifest",
        )
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("file") not in valid_names:
                continue
            name = cache + entry["file"]
            record = read(name)
            require(fingerprints.get(name) == entry.get("sha256"), "cache_file_hash")
            try:
                request, response = record["request"], record["response"]
                url = urlsplit(request["url"])
                query = dict(parse_qsl(url.query))
                require(
                    url.scheme == "https"
                    and url.hostname == "api.travelpayouts.com"
                    and url.path == "/aviasales/v3/prices_for_dates",
                    "cache_request",
                )
                require(
                    all(
                        entry[field] == query[key]
                        for field, key in (
                            ("origin", "origin"),
                            ("destination", "destination"),
                            ("departure_date", "departure_at"),
                        )
                    ),
                    "cache_request",
                )
                require(
                    not any("token" in key.lower() or "key" in key.lower() for key in query),
                    "cache_credentials",
                )
                headers = request["headers"] + response["headers"]
                require(
                    not any(
                        key.lower() in {"authorization", "x-access-token", "cookie", "set-cookie"} for key, _ in headers
                    ),
                    "cache_credentials",
                )
                raw = base64.b64decode(response["body_base64"], validate=True)
                require(
                    hashlib.sha256(raw).hexdigest() == response["body_sha256"] == entry["body_sha256"],
                    "cache_body_hash",
                )
                body = json.loads(raw)
                require(
                    response["status_code"] == 200
                    and body.get("success") is True
                    and isinstance(body.get("data"), list),
                    "cache_response",
                )
            except (ValueError, TypeError, KeyError, AttributeError):
                require(False, "cache_structure")
    else:
        require(False, "cache_manifest")
    digest = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
    return {
        "status": "failed" if failures else "passed",
        "counts": counts,
        "failures": failures,
        "data_sha256": digest,
    }


def coverage_cases() -> list[dict]:
    """The former exploratory 138-case probe, with explicit expected negatives."""
    dates = [
        TODAY + timedelta(days=1),
        REFERENCE,
        date(2026, 12, 31),
        date(2027, 1, 15),
        date(2027, 2, 28),
        date(2027, 6, 15),
        date(2027, 8, 15),
    ]
    groups = [
        ("solo", {"adults": 1}),
        ("family5", {"children_ages": [5]}),
        ("infant0", {"children_ages": [0]}),
        ("infant1", {"children_ages": [1]}),
        ("twins", {"children_ages": [5, 5]}),
        ("mixed_children", {"children_ages": [0, 5, 8, 17]}),
        ("pet", {"pets": 1}),
        ("family_pet", {"children_ages": [5, 8], "pets": 1}),
        ("four_adults", {"adults": 4}),
        (
            "maximum_group",
            {"adults": 12, "children_ages": [0, 1, 5, 5, 8, 8, 17, 17], "pets": 3},
        ),
        ("refundable", {"refundable_only": True}),
        ("five_star", {"min_stars": 5}),
        ("direct", {"direct_only": True}),
        ("late", {"late_departure": True}),
    ]
    cases = []

    def add(label, brief, *, empty=False, kind="package", sources=None):
        cases.append(
            {
                "case": label,
                "brief": {"adults": 2, "nights": 7, **brief},
                "expected": "empty" if empty else "nonempty",
                "kind": kind,
                "sources": sources,
            }
        )

    for origin, destination in product(("MOW", "LED"), ("AYT", "DXB", "MLE")):
        route = {"origin": origin, "destination": destination}
        for day in dates:
            sources = None
            if origin == "MOW" and day == REFERENCE:
                sources = [
                    "deterministic_mock" if destination == "DXB" else "provider_replay",
                    "provider_replay",
                ]
            add(
                f"date/{origin}/{destination}/{day}",
                {**route, "start_date": day},
                sources=sources,
            )
        for label, values in groups:
            empty = (origin, destination, label) in {
                ("LED", "MLE", "direct"),
                ("MOW", "MLE", "late"),
            }
            add(
                f"{label}/{origin}/{destination}",
                {**route, "start_date": REFERENCE, **values},
                empty=empty,
            )
        for nights in (1, 60):
            add(
                f"nights{nights}/{origin}/{destination}",
                {**route, "start_date": REFERENCE, "nights": nights},
            )
    for destination, month, limit in (("DXB", 1, 395), ("AYT", 3, 305)):
        add(
            f"monthly/{destination}/limit{limit}",
            {
                "origin": "MOW",
                "destination": destination,
                "month": month,
                "year": 2027,
                "max_flight_minutes": limit,
            },
        )
    for destination, budget in (("AYT", 1000), ("MLE", 10000)):
        add(
            f"budget/{destination}/{budget}",
            {
                "origin": "MOW",
                "destination": destination,
                "start_date": REFERENCE,
                "budget_rub": budget,
            },
            empty=True,
        )
    for origin, destination, seat in (
        ("MOW", "LED", "seated"),
        ("LED", "MOW", "coupe"),
    ):
        add(
            f"train/{origin}/{destination}/{seat}",
            {
                "transport": "train",
                "origin": origin,
                "destination": destination,
                "start_date": REFERENCE,
                "seat_class": seat,
                "children_ages": [0, 5],
                "pets": 1,
            },
            kind="train",
        )
    for origin, destination, seat in (
        ("MOW", "LED", "sv"),
        ("LED", "MOW", "platzkart"),
    ):
        add(
            f"train/{origin}/{destination}/{seat}",
            {
                "transport": "train",
                "origin": origin,
                "destination": destination,
                "start_date": REFERENCE,
                "seat_class": seat,
            },
            kind="train",
        )
    return cases


def check_options(case: dict, brief: TripBrief, options: list[dict]) -> list[str]:
    failures = []

    def require(condition, code):
        if not condition and code not in failures:
            failures.append(code)

    require(bool(options) == (case["expected"] == "nonempty"), "expected_result")
    if case["kind"] == "train":
        for option in options:
            require(
                option.get("schedule_source") == "synthetic" and option.get("is_synthetic") is True,
                "train_provenance",
            )
            require(option.get("seat_class") == brief.seat_class, "train_class")
            require(type(option.get("price_rub")) is int and option["price_rub"] > 0, "train_group_price")
        return failures
    if options:
        require(
            any(option.get("can_confirm") is True for option in options),
            "no_confirmable_demo_option",
        )
    for option in options:
        hotel, transfer = option["hotel"], option.get("transfer")
        require(
            option.get("travelers") == brief.travelers.model_dump(mode="json"),
            "travelers",
        )
        require(option.get("party_price_scope") == "whole_party", "group_scope")
        require(bool(HOTEL_ID.fullmatch(hotel.get("hotel_id", ""))), "hotel_identity")
        require(
            not brief.children_ages or hotel.get("adults_only") is False,
            "family_policy",
        )
        require(not brief.pets or hotel.get("pets_allowed") is True, "pet_policy")
        require(
            not brief.refundable_only or hotel.get("refundable") is True,
            "refund_policy",
        )
        require(
            brief.min_stars is None or (hotel.get("stars") or 0) >= brief.min_stars,
            "stars",
        )
        require(hotel.get("nights") == brief.nights, "nights")
        total = (
            sum(option[leg][key] for leg in ("outbound", "inbound") for key in ("price_rub", "baggage_price_rub"))
            + hotel["price_rub"]
        )
        total += transfer["price_rub"] if transfer else 0
        require(option.get("known_total_rub") == total, "component_sum")
        require(
            option.get("total_rub") == (total if transfer else None),
            "unknown_total_not_zero",
        )
        require(brief.budget_rub is None or total <= brief.budget_rub, "budget")
        for index, leg in enumerate(("outbound", "inbound")):
            flight = option[leg]
            require(
                flight.get("demo_source", {}).get("kind") in {"provider_replay", "deterministic_mock"},
                "flight_provenance",
            )
            if case["sources"]:
                require(
                    flight["demo_source"]["kind"] == case["sources"][index],
                    "expected_source",
                )
            require(not brief.direct_only or flight.get("stops") == 0, "direct")
            if leg == "outbound" and brief.late_departure:
                hour = datetime.fromisoformat(flight["departure_at"]).hour
                require(hour >= 18 or hour < 6, "late_departure")
            if brief.max_flight_minutes is not None:
                elapsed = datetime.fromisoformat(flight["arrival_at"]).astimezone(UTC) - datetime.fromisoformat(
                    flight["departure_at"]
                ).astimezone(UTC)
                require(
                    elapsed.total_seconds() <= brief.max_flight_minutes * 60,
                    "flight_duration",
                )
    return failures


async def check_coverage(data_dir: Path = ROOT / "data") -> dict:
    started = time.monotonic()
    report = {
        "schema_version": 1,
        "scope": "data_and_planner_only",
        "reference_today": TODAY.isoformat(),
        "status": "failed",
        "credentials_used": False,
        "network_used": False,
        "model_calls": 0,
        "acceptance": {
            "chat": False,
            "booking": False,
            "external_model": False,
            "supplier_availability": False,
        },
        "cases": [],
        "failures": [],
    }
    with forbid_external_network() as attempts:
        report["inventory"] = check_data(Path(data_dir))
        report["failures"] = list(report["inventory"]["failures"])
        cases = coverage_cases()
        if not cases:
            report["failures"].append("no_planner_cases")
        if not report["failures"]:
            tools = create_tools(offline_settings(Path(data_dir)))
            tool_calls = []

            async def call(name, arguments):
                tool_calls.append((name, arguments))
                return await run_tool(name, arguments, tools=tools)

            try:
                for case in cases:
                    row = {
                        "case": case["case"],
                        "expected": case["expected"],
                        "status": "failed",
                    }
                    try:
                        tool_calls.clear()
                        brief = TripBrief(**case["brief"])
                        plan = (
                            await search_train_trip(brief, call)
                            if case["kind"] == "train"
                            else await search_trip(brief, call, TODAY)
                        )
                        failures = check_options(case, brief, plan.options)
                        if case["kind"] == "train" and not any(
                            name == "search_trains" and arguments.get("travelers") == brief.travelers.model_dump()
                            for name, arguments in tool_calls
                        ):
                            failures.append("train_group_request")
                        if case["expected"] == "empty" and not plan.notes:
                            failures.append("empty_without_reason")
                        row.update(
                            status="failed" if failures else "passed",
                            option_count=len(plan.options),
                            failures=failures,
                        )
                    except Exception:
                        # Never echo arbitrary provider bodies, keys or runtime IDs.
                        row["failures"] = ["planner_error"]
                    report["cases"].append(row)
            finally:
                await tools.aclose()
        if attempts:
            report["failures"].append("network_attempt_blocked")
        report["network_attempts_blocked"] = len(attempts)
    report["case_counts"] = dict(Counter(row["status"] for row in report["cases"]))
    report["case_counts"]["total"] = len(report["cases"])
    if report["case_counts"].get("failed"):
        report["failures"].append("planner_cases_failed")
    report["status"] = "failed" if report["failures"] else "passed"
    report["seconds"] = round(time.monotonic() - started, 3)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional public JSON report outside the data directory",
    )
    args = parser.parse_args(argv)
    if args.output and args.output.resolve().is_relative_to(args.data_dir.resolve()):
        parser.error("The report must be outside the read-only data directory")
    report = asyncio.run(check_coverage(args.data_dir))
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    summary = {key: report[key] for key in ("status", "scope", "case_counts", "failures", "seconds")}
    print(
        json.dumps(summary, ensure_ascii=False) if args.output else text,
        end="\n" if args.output else "",
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
