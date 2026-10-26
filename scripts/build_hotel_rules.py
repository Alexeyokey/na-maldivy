#!/usr/bin/env python3
"""Rebuild D3 synthetic hotel rules from the checked catalogue, entirely offline.

These policies and prices are demonstration fixtures, never statements about
actual OSM hotels. The generator uses only the Python standard library and does
not read provider configuration, reviews or profiles. --check is read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = "synthetic-hotel-rules-v2"
HOTEL_ID = re.compile(r"osm:(node|way|relation)/([1-9][0-9]*)\Z")


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
                    "version": "synthetic-hotel-v2",
                    "seed": seed,
                    "is_synthetic": True,
                    "scenario": scenario,
                    "notice": (
                        "Все поля этой записи вымышлены для демонстрации и не являются правилами реального OSM-отеля."
                    ),
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
                    "pricing_note": (
                        "Synthetic RUB per room/night, two people per room; children count as full places. "
                        "Seasonal factor is deterministic; pet surcharge is once per pet per stay, "
                        "included in group total."
                    ),
                }
            )
    return sorted(rows, key=lambda item: item["hotel_id"])


def build_output(root: Path = ROOT) -> dict:
    hotels = json.loads(
        (root / "data/hotels/catalog.json").read_text(encoding="utf-8")
    )["hotels"]
    seen = set()
    for hotel in hotels:
        hotel_id = hotel["hotel_id"]
        if not HOTEL_ID.fullmatch(hotel_id) or hotel_id in seen:
            raise ValueError(f"Invalid or duplicate hotel identity: {hotel_id}")
        if hotel["destination"] not in {"AYT", "DXB", "MLE"}:
            raise ValueError(f"Unsupported destination: {hotel['destination']}")
        seen.add(hotel_id)
    return {
        "schema_version": 1,
        "generator": GENERATOR,
        "is_synthetic": True,
        "not_real_hotel_policies": True,
        "hotels": synthetic_rules(hotels),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the complete output without writing or network",
    )
    args = parser.parse_args()
    output = ROOT / "data/hotels/synthetic_rules.json"
    document = build_output()
    expected = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != expected:
            raise SystemExit(
                "Synthetic hotel rules are stale; run scripts/build_hotel_rules.py"
            )
    else:
        output.write_text(expected, encoding="utf-8")
    print(
        f"{'Checked' if args.check else 'Built'} {len(document['hotels'])} synthetic hotel rules; network requests: 0"
    )


if __name__ == "__main__":
    main()
