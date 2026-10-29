#!/usr/bin/env python3
"""Reproduce fictional hotels/reviews/profiles without network, keys or model calls.

Only data/demo outputs are written. Original OSM facts, rules and the sixty
existing review/profile pairs are read-only inputs and are never regenerated.
"""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.contracts.hotel_profiles import HotelReviewProfile
from app.tools.destinations import load_catalogue
from app.tools.expanded_hotels import FICTIONAL_NOTICE, GENERATOR, ExpandedHotelCatalogue
from app.tools.hotel_profiles import (
    GENERATOR as REVIEW_GENERATOR,
)
from app.tools.hotel_profiles import build_profile, extract_lexical, make_synthetic_reviews

# The fixed ordering is part of the stable hotel identity, never a real ranking.
SCENARIOS = (
    ("family_budget", "Семейный бюджетный", 3, 100, False, True, True, True, True),
    ("budget_nonrefundable", "Эконом без возврата", 3, 85, False, False, False, True, False),
    ("adults_quiet", "Тихий только для взрослых", 4, 145, True, False, True, True, True),
    ("family_comfort", "Семейный комфорт", 4, 150, False, True, True, True, True),
    ("pets_value", "С питомцем", 3, 110, False, True, False, False, False),
    ("late_safe", "Позднее прибытие", 4, 135, False, False, True, True, True),
    ("family_premium", "Семейный премиум", 5, 210, False, True, True, True, True),
    ("limited_reception", "Ограниченная стойка", 5, 170, False, False, False, False, False),
)


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def number(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


def build_documents(data_dir: Path) -> dict[Path, bytes]:
    """Pure deterministic projection of D5 anchors and the original hotel IDs."""
    catalogue_path = data_dir / "destinations.yaml"
    catalogue = load_catalogue(catalogue_path)
    original = json.loads((data_dir / "hotels/catalog.json").read_bytes())["hotels"]
    covered = {row["destination"] for row in original}
    destinations = sorted((row for row in catalogue.destinations if row.iata not in covered), key=lambda row: row.iata)
    entries = []
    documents = {}
    for destination in destinations:
        base = 2400 + number(f"hotel-price-v1:{destination.iata}") % 2401
        for index, (scenario, title, stars, multiplier, adults_only, pets, refundable, reception, early) in enumerate(
            SCENARIOS, start=1
        ):
            hotel_id = f"demo:hotel/{destination.iata}-{index:02d}"
            seed = number(f"hotel-rules-v1:{hotel_id}")
            angle = (number(f"hotel-angle-v1:{destination.iata}") % 360 + index * 43) * math.pi / 180
            radius_km = 3 + index * 1.25
            latitude = destination.location.latitude + radius_km * math.sin(angle) / 111.32
            longitude = destination.location.longitude + radius_km * math.cos(angle) / (
                111.32 * max(0.05, math.cos(math.radians(destination.location.latitude)))
            )
            longitude = (longitude + 180) % 360 - 180
            safe = not adults_only and pets and refundable and reception
            rules = {
                "hotel_id": hotel_id,
                "version": "synthetic-demo-hotel-v1",
                "seed": seed,
                "is_synthetic": True,
                "scenario": scenario,
                "notice": FICTIONAL_NOTICE,
                "always_available": index == 1,
                "safe_rule": "family_pets_refundable_24h" if safe else None,
                "base_room_night_rub": base * multiplier // 100,
                "room_capacity_people": 4,
                "room_capacity_adults": 2,
                "infant_max_age": 2,
                "adults_only": adults_only,
                "pets_allowed": pets,
                "refundable": refundable,
                "pet_stay_surcharge_rub": 1000,
                "check_in_time": "14:00:00",
                "check_out_time": "12:00:00",
                "reception_24h": reception,
                "reception_closes_at": None if reception else "22:00:00",
                "early_check_in_available": early,
                "synthetic_airport_transfer_minutes": None,
                "synthetic_airport_transfer_source": None,
                "synthetic_beach_distance_m": 350 + index * 175 if "beach" in destination.tags else 9000,
                "synthetic_rating": round(7.1 + index * 0.2 + seed % 5 / 10, 1),
                "synthetic_amenities": ["wifi", "breakfast"] + ([] if adults_only else ["family_room"]),
                "pricing_note": (
                    "Fictional RUB per room/night; two adults and four paying guests per room. "
                    "Infants under two do not add rooms; pet surcharge is once per pet/stay. "
                    "Deterministic seasonal factor applies. Always-available means a synthetic demo fixture, "
                    "never supplier inventory."
                ),
            }
            entries.append(
                {
                    "hotel_id": hotel_id,
                    "destination": destination.iata,
                    "name": f"Демо · {destination.name} · {title} {index:02d}",
                    "name_en": None,
                    "latitude": round(latitude, 6),
                    "longitude": round(longitude, 6),
                    "timezone": destination.location.timezone,
                    "coordinate_kind": "synthetic_offset_from_airport",
                    "stars": stars,
                    "source_kind": "fictional_demo",
                    "is_fictional": True,
                    "rules": rules,
                }
            )
            reviews = make_synthetic_reviews(hotel_id, rules)
            extracted = extract_lexical(reviews)
            if any(row.mentions != review.annotations for row, review in zip(extracted, reviews, strict=True)):
                raise ValueError("Lexical review extraction disagrees with the synthetic annotations")
            profile = build_profile(reviews, extracted)
            HotelReviewProfile.model_validate(profile)
            filename = hotel_id.replace(":", "-").replace("/", "-") + ".json"
            documents[Path("demo/hotel_reviews") / filename] = encoded(
                {
                    "schema_version": 1,
                    "hotel_id": hotel_id,
                    "is_synthetic": True,
                    "generator": REVIEW_GENERATOR,
                    "real_guest_reviews_used": False,
                    "reviews": [review.model_dump(mode="json") for review in reviews],
                }
            )
            documents[Path("demo/hotel_profiles") / filename] = encoded(profile)
    document = {
        "schema_version": 1,
        "generator": GENERATOR,
        "is_synthetic": True,
        "notice": FICTIONAL_NOTICE,
        "source_catalog_sha256": hashlib.sha256(catalogue_path.read_bytes()).hexdigest(),
        "coordinate_method": (
            "Deterministic fictional offsets 4.25–13 km from D5 airport anchors; "
            "not real hotel coordinates, road distances or supplier facts."
        ),
        "destinations": [row.iata for row in destinations],
        "hotels": entries,
    }
    ExpandedHotelCatalogue.model_validate(document)
    documents[Path("demo/hotels.generated.json")] = encoded(document)
    return documents


def run(data_dir: Path, *, check: bool = False) -> dict:
    data_dir = data_dir.resolve()
    documents = build_documents(data_dir)
    for folder in (Path("demo/hotel_reviews"), Path("demo/hotel_profiles")):
        actual = {path.relative_to(data_dir) for path in (data_dir / folder).glob("*.json")}
        expected = {path for path in documents if path.parent == folder}
        if actual - expected or check and actual != expected:
            raise ValueError("Fictional corpus/profile files disagree with the generated hotel identities")
    for relative, expected in documents.items():
        path = data_dir / relative
        if check:
            if not path.exists() or path.read_bytes() != expected:
                raise ValueError("Fictional hotel data differ from deterministic generated bytes")
        elif not path.exists() or path.read_bytes() != expected:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
    hotels = json.loads(documents[Path("demo/hotels.generated.json")])["hotels"]
    return {
        "status": "passed",
        "destinations": len({row["destination"] for row in hotels}),
        "hotels": len(hotels),
        "reviews": 24 * len(hotels),
        "profiles": len(hotels),
        "read_only_check": check,
        "live_model_calls": 0,
        "network_calls": 0,
        "source_kind": "fictional_demo",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        report = run(args.data_dir, check=args.check)
    except (ValueError, OSError, KeyError, TypeError):
        print(
            "Fictional hotel build/check failed: source catalogue or generated data are inconsistent.", file=sys.stderr
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
