#!/usr/bin/env python3
"""Build truthful synthetic demo policies/reviews and auditable hotel profiles.

Default execution is entirely offline and never reads API keys. --mode llm is
an optional explicit live-model operation and requires --allow-live-llm plus a
configured non-fake provider. The checked snapshots use --mode lexical.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.tools.hotel_profiles import (
    GENERATOR,
    SyntheticReview,
    build_profile,
    extract_lexical,
    extract_reviews_llm,
    make_synthetic_reviews,
)
from app.tools.hotels import hotel_id_filename, normalize_hotel_id


def synthetic_rules(hotels: list[dict]) -> list[dict]:
    rows = []
    by_destination = {}
    for hotel in hotels:
        by_destination.setdefault(hotel["destination"], []).append(hotel)
    scenarios = (
        "cheap_nonrefundable",
        "adults_only",
        "early_arrival",
        "far_transfer",
        "no_pets",
    )
    for destination, values in by_destination.items():
        for index, hotel in enumerate(
            sorted(values, key=lambda item: item["hotel_id"])
        ):
            seed = int(
                hashlib.sha256(
                    hotel["hotel_id"].replace("/", ":").encode()
                ).hexdigest()[:8],
                16,
            )
            scenario = scenarios[index % len(scenarios)]
            base = {"AYT": 4500, "DXB": 6500, "MLE": 8000}[destination] + (
                seed % 30
            ) * 100
            if scenario == "cheap_nonrefundable":
                base = base * 65 // 100
            rows.append(
                {
                    "hotel_id": hotel["hotel_id"],
                    "version": "synthetic-hotel-v1",
                    "seed": seed,
                    "is_synthetic": True,
                    "scenario": scenario,
                    "notice": "Все поля этой записи вымышлены для демонстрации и не являются правилами реального OSM-отеля.",
                    "base_room_night_rub": base,
                    "room_capacity_people": 2,
                    "adults_only": scenario == "adults_only",
                    "pets_allowed": scenario not in {"no_pets", "adults_only"},
                    "refundable": scenario != "cheap_nonrefundable",
                    "pet_stay_surcharge_rub": 1000,
                    "check_in_time": "14:00:00",
                    "check_out_time": "12:00:00",
                    "reception_24h": scenario != "early_arrival",
                    "reception_closes_at": "22:00:00"
                    if scenario == "early_arrival"
                    else None,
                    "early_check_in_available": scenario != "early_arrival",
                    "synthetic_airport_transfer_minutes": 120
                    if scenario == "far_transfer"
                    else None,
                    "synthetic_airport_transfer_source": "synthetic_scenario_override"
                    if scenario == "far_transfer"
                    else None,
                    "synthetic_beach_distance_m": 6000
                    if scenario == "far_transfer"
                    else 400 + seed % 600,
                    "synthetic_rating": round(7.0 + (seed % 26) / 10, 1),
                    "synthetic_amenities": ["wifi", "breakfast"]
                    + (["family_room"] if scenario != "adults_only" else []),
                    "pricing_note": "Synthetic RUB per room/night, two people per room; children count as full places. "
                    "Seasonal factor is deterministic; pet surcharge is once per pet per stay, included in group total.",
                }
            )
    return sorted(rows, key=lambda item: item["hotel_id"])


async def run(args: argparse.Namespace) -> None:
    hotels_dir = ROOT / "data/hotels"
    hotels = json.loads((hotels_dir / "catalog.json").read_text())["hotels"]
    rules_path = hotels_dir / "synthetic_rules.json"
    reviews_dir = ROOT / "data/hotel_reviews"
    if not args.check:
        reviews_dir.mkdir(exist_ok=True)
    profiles_dir = ROOT / "data/hotel_profiles"
    if not args.check:
        profiles_dir.mkdir(exist_ok=True)
    if args.check and (args.generate_demo_data or args.mode != "lexical"):
        raise ValueError(
            "--check is offline/read-only and requires lexical mode without --generate-demo-data"
        )
    expected_names = {hotel_id_filename(row["hotel_id"]) for row in hotels}
    if args.check:
        if {file.name for file in reviews_dir.glob("*.json")} != expected_names:
            raise ValueError("Corpus files do not cover exactly the hotel catalogue")
        if {file.name for file in profiles_dir.glob("*.json")} != expected_names:
            raise ValueError("Profile files do not cover exactly the hotel catalogue")
        if json.loads(rules_path.read_text())["hotels"] != synthetic_rules(hotels):
            raise ValueError(
                "Saved synthetic rules differ from deterministic generation"
            )
    if args.generate_demo_data:
        rules = synthetic_rules(hotels)
        rules_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "generator": GENERATOR,
                    "is_synthetic": True,
                    "not_real_hotel_policies": True,
                    "hotels": rules,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        for row in rules:
            reviews = make_synthetic_reviews(row["hotel_id"], row)
            (reviews_dir / (hotel_id_filename(row["hotel_id"]))).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "hotel_id": row["hotel_id"],
                        "is_synthetic": True,
                        "generator": GENERATOR,
                        "real_guest_reviews_used": False,
                        "reviews": [
                            review.model_dump(mode="json") for review in reviews
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
    client = None
    if args.mode == "llm":
        if not args.allow_live_llm:
            raise ValueError(
                "--mode llm requires --allow-live-llm; no provider has been contacted"
            )
        from app.config import Settings
        from app.llm import create_llm_client

        settings = Settings()
        if settings.llm_provider == "fake":
            raise ValueError(
                "Live extraction needs an explicitly configured non-fake provider"
            )
        client = create_llm_client(settings)
    try:
        count = 0
        for file in sorted(reviews_dir.glob("*.json")):
            data = json.loads(file.read_text())
            reviews = [SyntheticReview.model_validate(row) for row in data["reviews"]]
            if not 20 <= len(reviews) <= 50:
                raise ValueError(f"{file}: expected 20..50 synthetic reviews")
            if args.hotel_id and data["hotel_id"] != normalize_hotel_id(args.hotel_id):
                continue
            if client:
                extracted, model = await extract_reviews_llm(client, reviews)
                profile = build_profile(
                    reviews, extracted, method="llm-literal-evidence-v1", model=model
                )
            else:
                extracted = extract_lexical(reviews)
                if any(
                    set(item.mentions) != set(review.annotations)
                    for item, review in zip(extracted, reviews, strict=True)
                ):
                    raise ValueError(
                        f"Lexical extraction differs from fixture annotation: {file}"
                    )
                profile = build_profile(reviews, extracted)
            profile_path = profiles_dir / file.name
            if args.check:
                if json.loads(profile_path.read_text()) != profile:
                    raise ValueError(
                        f"Saved profile differs from reproducible output: {profile_path}"
                    )
            else:
                profile_path.write_text(
                    json.dumps(profile, ensure_ascii=False, indent=2) + "\n"
                )
            count += 1
        print(
            f"{'Checked' if args.check else 'Built'} {count} profiles using {args.mode}; live model calls: {client is not None}"
        )
    finally:
        if client:
            await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare checked profiles and rules without writing or network",
    )
    parser.add_argument("--generate-demo-data", action="store_true")
    parser.add_argument("--mode", choices=("lexical", "llm"), default="lexical")
    parser.add_argument("--allow-live-llm", action="store_true")
    parser.add_argument("--hotel-id")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
