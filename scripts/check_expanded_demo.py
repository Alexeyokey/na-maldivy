#!/usr/bin/env python3
"""Read-only 48-destination demo inventory/planner check; no env, model or network.

Only an explicitly requested report outside data/ is written. This is not chat,
checkout, real inventory, a current visa check or a browser acceptance report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import UTC, date, datetime
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from check_demo_coverage import check_data as check_original_data  # noqa: E402
from check_demo_coverage import forbid_external_network, offline_settings  # noqa: E402

from app.agent.brief import TripBrief  # noqa: E402
from app.agent.coverage import full_trip_destinations  # noqa: E402
from app.agent.planner import search_trip  # noqa: E402
from app.contracts.hotel_profiles import HotelReviewProfile  # noqa: E402
from app.contracts.tools import Place  # noqa: E402
from app.tools.destinations import load_catalogue  # noqa: E402
from app.tools.expanded_hotels import ExpandedHotelCatalogue  # noqa: E402
from app.tools.hotel_profiles import SyntheticReview, build_profile, extract_lexical, make_synthetic_reviews  # noqa: E402
from app.tools.registry import create_tools, run_tool  # noqa: E402
from app.tools.transfers import HotelTransfers  # noqa: E402

TODAY = date(2026, 10, 29)
BASELINE = frozenset({"AYT", "DXB", "MLE"})
DEMO_ID = re.compile(r"demo:hotel/([A-Z]{3})-0[1-8]\Z")
OSM_ID = re.compile(r"osm:(?:node|way|relation)/[1-9][0-9]*\Z")
PORTFOLIO = (
    ("mixed_family", {"children_ages": [0, 1, 5, 8, 17]}),
    ("twins", {"children_ages": [5, 5]}),
    ("four_adults", {"adults": 4}),
    ("pet_refundable_five_star", {"pets": 1, "refundable_only": True, "min_stars": 5}),
    ("late", {"late_departure": True}),
    ("budget_impossible", {"budget_rub": 1}),
    ("duration_impossible", {"max_flight_minutes": 1}),
)


def check_inventory(data_dir: Path) -> dict:
    """Validate both source layers before any planner/registry is constructed."""
    root = Path(data_dir).resolve()
    failures, fingerprints = [], {}
    counts = {"destinations": 0, "hotels": 0, "fictional_hotels": 0, "reviews": 0, "profiles": 0}

    def require(condition, code):
        if not condition and code not in failures:
            failures.append(code)

    def read(relative):
        path = root / relative
        if not path.resolve().is_relative_to(root):
            raise ValueError("Data reference escapes its root")
        raw = path.read_bytes()
        fingerprints[relative] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    try:
        original = check_original_data(root)
        failures.extend("original_" + code for code in original["failures"])
        catalogue = load_catalogue(root / "destinations.yaml")
        destinations = {row.iata: row for row in catalogue.destinations}
        require(len(destinations) == 48, "destination_count")
        counts["destinations"] = len(destinations)
        raw_hotels = read("demo/hotels.generated.json")
        supplement = ExpandedHotelCatalogue.model_validate(raw_hotels)
        require(set(supplement.destinations) == set(destinations) - BASELINE, "supplement_destinations")
        require(
            supplement.source_catalog_sha256 == hashlib.sha256((root / "destinations.yaml").read_bytes()).hexdigest(),
            "catalogue_source_hash",
        )
        hotels = {row.hotel_id: row for row in supplement.hotels}
        ids = set(hotels)
        require(len(ids) == 360, "fictional_hotel_count")
        counts["fictional_hotels"] = len(ids)
        counts["hotels"] = original["counts"]["hotels"] + len(ids)
        require(counts["hotels"] == 420, "combined_hotel_count")
        expected_names = {identity.replace(":", "-").replace("/", "-") + ".json" for identity in ids}
        for directory in ("demo/hotel_reviews", "demo/hotel_profiles"):
            require({p.name for p in (root / directory).glob("*.json")} == expected_names, "supplement_profile_ids")
        for identity, hotel in sorted(hotels.items()):
            require(bool(DEMO_ID.fullmatch(identity)), "fictional_identity")
            require(hotel.name.startswith("Демо · "), "fictional_name_notice")
            require(hotel.timezone == destinations[hotel.destination].location.timezone, "hotel_timezone")
            filename = identity.replace(":", "-").replace("/", "-") + ".json"
            corpus = read("demo/hotel_reviews/" + filename)
            raw_profile = read("demo/hotel_profiles/" + filename)
            reviews = [SyntheticReview.model_validate(row) for row in corpus["reviews"]]
            profile = HotelReviewProfile.model_validate(raw_profile)
            require(
                corpus.get("hotel_id") == profile.hotel_id == identity
                and all(row.hotel_id == identity for row in reviews),
                "profile_hotel_identity",
            )
            require(len(reviews) == profile.review_count == 24, "review_count")
            require(
                corpus.get("is_synthetic") is True
                and corpus.get("real_guest_reviews_used") is False
                and profile.is_synthetic
                and profile.extraction.live_model_run is False,
                "profile_provenance",
            )
            require(len({row.review_id for row in reviews}) == 24, "review_identity")
            rules = hotel.rules.model_dump(mode="json")
            require(reviews == make_synthetic_reviews(identity, rules), "corpus_reproduction")
            extracted = extract_lexical(reviews)
            require(
                all(row.mentions == review.annotations for row, review in zip(extracted, reviews, strict=True)),
                "literal_evidence",
            )
            require(profile.model_dump(mode="json") == build_profile(reviews, extracted), "profile_reproduction")
            for aspect, forbidden in (("children", rules["adults_only"]), ("pets", not rules["pets_allowed"])):
                require(not forbidden or profile.aspects[aspect].positive_count == 0, "profile_policy")
            counts["reviews"] += len(reviews)
            counts["profiles"] += 1
        counts["reviews"] += original["counts"]["reviews"]
        counts["profiles"] += original["counts"]["profiles"]
        require(counts["reviews"] == 10080 and counts["profiles"] == 420, "combined_profile_count")
        network = read("demo/routes.generated.json")
        require(network.get("demo_source") == "deterministic_mock", "route_provenance")
        airports = {code: Place.model_validate(row) for code, row in network["airports"].items()}
        require(set(destinations) | {"SVO", "LED"} == set(airports), "airport_coverage")
        for code, airport in airports.items():
            require(airport.iata == code and airport.kind == "airport", "airport_identity")
            if code in destinations:
                require(airport.timezone == destinations[code].location.timezone, "airport_timezone")
        original_routes = read("routes/network.json")["routes"]
        original_pairs = {(row["origin"], row["destination"]) for row in original_routes}
        expected_pairs = {
            (origin, destination)
            for origin, destination in product(("SVO", "LED"), destinations)
            if origin != destination and (origin, destination) not in original_pairs
        }
        pairs = [(row["origin"], row["destination"]) for row in network["routes"]]
        require(set(pairs) == expected_pairs and len(pairs) == len(expected_pairs) == 89, "route_coverage")
        for row in network["routes"]:
            require(
                row.get("sources") == ["deterministic_mock"]
                and row.get("demo_source") == "deterministic_mock"
                and row["origin"] != row["destination"]
                and row.get("stops") == 0
                and row.get("via") == []
                and type(row.get("synthetic_duration_minutes")) is int
                and row["synthetic_duration_minutes"] > 0
                and type(row.get("synthetic_base_price_rub")) is int
                and row["synthetic_base_price_rub"] > 0,
                "route_model",
            )
        raw_transfers = read("demo/transfers.generated.json")
        require(raw_transfers.get("demo_source") == "deterministic_mock", "transfer_provenance")
        transfers = [HotelTransfers.model_validate(row) for row in raw_transfers["transfers"]]
        require({row.hotel_id for row in transfers} == ids and len(transfers) == len(ids), "transfer_hotel_ids")
        for row in transfers:
            hotel = hotels[row.hotel_id]
            require(row.airport == hotel.destination and row.tz == hotel.timezone, "transfer_route_timezone")
            require(all(option.source == "synthetic" for option in row.options), "fictional_transfer_source")
            require(
                any(
                    option.mode == "private_minivan"
                    and option.hours is None
                    and option.pets is True
                    and option.child_seat is True
                    and option.prebook_hours == 0
                    for option in row.options
                ),
                "safe_family_transfer",
            )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        require(False, "malformed_expanded_data")
    digest = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
    return {"status": "failed" if failures else "passed", "counts": counts, "failures": failures, "data_sha256": digest}


def coverage_cases(destinations):
    """1152 explicit-date baselines, then bounded new-destination constraints."""
    cases = []
    for origin, destination, month in product(("MOW", "LED"), sorted(destinations), range(1, 13)):
        cases.append(
            {
                "case": f"calendar/{origin}/{destination}/2027-{month:02d}",
                "matrix": "A",
                "expected": "empty" if origin == destination else "nonempty",
                "brief": {"origin": origin, "destination": destination, "start_date": date(2027, month, 15)},
            }
        )
    for origin, destination in product(("MOW", "LED"), sorted(set(destinations) - BASELINE)):
        for label, values in PORTFOLIO:
            cases.append(
                {
                    "case": f"{label}/{origin}/{destination}",
                    "matrix": "B",
                    "expected": "empty" if origin == destination or label.endswith("impossible") else "nonempty",
                    "brief": {"origin": origin, "destination": destination, "start_date": date(2027, 6, 15), **values},
                }
            )
    return cases


def check_options(case, brief, options):
    failures = []

    def require(condition, code):
        if not condition and code not in failures:
            failures.append(code)

    require(bool(options) == (case["expected"] == "nonempty"), "expected_result")
    if options:
        require(any(row.get("can_confirm") is True for row in options), "no_confirmable_option")
    airport = "SVO" if brief.origin == "MOW" else brief.origin
    for row in options:
        hotel, transfer = row["hotel"], row.get("transfer")
        identity = hotel["hotel_id"]
        fictional = brief.destination not in BASELINE
        require(bool((DEMO_ID if fictional else OSM_ID).fullmatch(identity)), "hotel_namespace")
        require(not fictional or identity.startswith(f"demo:hotel/{brief.destination}-"), "hotel_destination")
        require(row["destination"] == brief.destination, "destination")
        require(row.get("travelers") == brief.travelers.model_dump(mode="json"), "party")
        require(row.get("party_price_scope") == "whole_party", "price_scope")
        require(not brief.children_ages or hotel.get("adults_only") is False, "children_policy")
        require(not brief.pets or hotel.get("pets_allowed") is True, "pet_policy")
        require(not brief.refundable_only or hotel.get("refundable") is True, "refund_policy")
        require(brief.min_stars is None or (hotel.get("stars") or 0) >= brief.min_stars, "stars")
        require(hotel["nights"] == brief.nights, "nights")
        require(hotel.get("review_profile", {}).get("hotel_id") == identity, "attached_profile_identity")
        require(hotel.get("review_profile", {}).get("is_synthetic") is True, "attached_profile_provenance")
        require(row["outbound"]["departure_at"][:10] == brief.start_date.isoformat(), "explicit_date")
        require(row["outbound"]["from"] == row["inbound"]["to"] == airport, "origin_route")
        require(row["outbound"]["to"] == row["inbound"]["from"] == brief.destination, "return_route")
        require(row["outbound"]["arrival_at"][:10] == hotel["check_in"], "checkin_date")
        require(row["inbound"]["departure_at"][:10] == hotel["check_out"], "checkout_date")
        if row.get("can_confirm") is True:
            require(transfer is not None and row.get("validation_status") == "checked", "confirmable_validation")
            require(not any(issue["severity"] == "critical" for issue in row.get("issues", [])), "critical_issue")
        if transfer:
            require(not fictional or transfer["id"].startswith(identity + ":"), "transfer_hotel_identity")
        total = sum(row[leg][key] for leg in ("outbound", "inbound") for key in ("price_rub", "baggage_price_rub"))
        total += hotel["price_rub"] + (transfer["price_rub"] if transfer else 0)
        require(row.get("known_total_rub") == total, "component_sum")
        require(row.get("total_rub") == (total if transfer else None), "unknown_price")
        require(brief.budget_rub is None or total <= brief.budget_rub, "budget")
        for leg in ("outbound", "inbound"):
            flight = row[leg]
            kind = flight.get("demo_source", {}).get("kind")
            require(kind in {"provider_replay", "deterministic_mock"}, "flight_provenance")
            require(not fictional or kind == "deterministic_mock", "fictional_route_source")
            require(kind != "deterministic_mock" or flight.get("price_source") is None, "mock_provider_claim")
            start, end = (datetime.fromisoformat(flight[key]) for key in ("departure_at", "arrival_at"))
            require(start.tzinfo is not None and end.tzinfo is not None and end > start, "aware_flight_time")
            if brief.max_flight_minutes is not None:
                require(
                    (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() <= brief.max_flight_minutes * 60,
                    "duration",
                )
            require(not brief.direct_only or flight["stops"] == 0, "direct")
            if leg == "outbound" and brief.late_departure:
                require(start.hour >= 18 or start.hour < 6, "late")
    return failures


async def check_coverage(data_dir: Path = ROOT / "data") -> dict:
    started = time.monotonic()
    report = {
        "schema_version": 1,
        "scope": "expanded_data_and_planner_only",
        "reference_today": TODAY.isoformat(),
        "status": "failed",
        "credentials_used": False,
        "network_used": False,
        "model_calls": 0,
        "acceptance": {"chat": False, "booking": False, "external_model": False, "supplier_availability": False},
        "failed_cases": [],
        "failures": [],
        "case_counts": {"passed": 0, "failed": 0, "total": 0},
        "matrices": {},
    }
    with forbid_external_network() as attempts:
        report["inventory"] = check_inventory(Path(data_dir))
        report["failures"] = list(report["inventory"]["failures"])
        if not report["failures"]:
            tools = None
            try:
                tools = create_tools(offline_settings(Path(data_dir)))
                destinations = {row.iata for row in load_catalogue(Path(data_dir) / "destinations.yaml").destinations}
                if full_trip_destinations(tools) != destinations:
                    report["failures"].append("configured_capability_coverage")
                else:

                    async def call(name, arguments):
                        return await run_tool(name, arguments, tools=tools)

                    cases = coverage_cases(destinations)
                    if len(cases) != 1782:
                        report["failures"].append("case_matrix_size")
                    for case in cases:
                        try:
                            brief = TripBrief(**{"adults": 2, "nights": 7, **case["brief"]})
                            plan = await search_trip(brief, call, TODAY)
                            failures = check_options(case, brief, plan.options)
                            if case["expected"] == "empty" and not plan.notes:
                                failures.append("empty_without_reason")
                        except Exception:
                            failures = ["planner_error"]
                        status = "failed" if failures else "passed"
                        report["case_counts"][status] += 1
                        report["case_counts"]["total"] += 1
                        matrix = report["matrices"].setdefault(case["matrix"], Counter())
                        matrix[status] += 1
                        matrix["total"] += 1
                        matrix["expected_empty"] += case["expected"] == "empty"
                        if failures:
                            report["failed_cases"].append({"case": case["case"], "failures": failures})
            except Exception:
                report["failures"].append("registry_or_coverage_error")
            finally:
                if tools is not None:
                    await tools.aclose()
        report["network_attempts_blocked"] = len(attempts)
    if report["network_attempts_blocked"]:
        report["failures"].append("network_attempt_blocked")
    if report["case_counts"]["failed"]:
        report["failures"].append("planner_cases_failed")
    report["status"] = "failed" if report["failures"] else "passed"
    report["seconds"] = round(time.monotonic() - started, 3)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, help="Optional anonymized JSON report outside data/")
    args = parser.parse_args(argv)
    if args.output and args.output.resolve().is_relative_to(args.data_dir.resolve()):
        parser.error("The report must be outside the read-only data directory")
    report = asyncio.run(check_coverage(args.data_dir))
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_bytes(encoded.encode("utf-8"))
    summary = {key: report[key] for key in ("status", "scope", "case_counts", "matrices", "failures", "seconds")}
    print(json.dumps(summary, ensure_ascii=False) if args.output else encoded, end="\n" if args.output else "")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
