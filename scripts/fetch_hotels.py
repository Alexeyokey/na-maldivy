#!/usr/bin/env python3
"""Project D3 facts from the shared, saved S4 OSM catalogue, without network.

The historical filename remains a compatibility entrypoint. Network refreshes
belong to backend/scripts/build_transfers.py fetch; then build transfers, run
this script, regenerate demo rules and run the D3 compatibility checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANCHORS = {
    "AYT": (36.8841, 30.7056),
    "DXB": (25.2048, 55.2708),
    "MLE": (4.1755, 73.5093),
}
ATTRIBUTION = "© OpenStreetMap contributors"
LICENSE = "https://opendatacommons.org/licenses/odbl/1-0/"


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    a, b = math.radians(lat1), math.radians(lat2)
    h = (
        math.sin((b - a) / 2) ** 2
        + math.cos(a) * math.cos(b) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(h)))


def build_outputs(root: Path = ROOT) -> dict[Path, dict]:
    directory = root / "data/hotels"
    content = (directory / "osm_hotels.json").read_bytes()
    snapshot = json.loads(content)
    hotels, seen = [], set()
    for row in snapshot["hotels"]:
        match = re.fullmatch(r"osm:(node|way|relation)/([1-9][0-9]*)", row["hotel_id"])
        if match is None or row["hotel_id"] in seen:
            raise ValueError(f"Invalid or duplicate hotel identity: {row['hotel_id']}")
        seen.add(row["hotel_id"])
        if (
            row["destination"] not in ANCHORS
            or row["tourism"] != "hotel"
            or not row["name"].strip()
        ):
            raise ValueError(f"Unsupported hotel facts: {row['hotel_id']}")
        latitude, longitude = row["lat"], row["lon"]
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError(f"Invalid hotel coordinates: {row['hotel_id']}")
        if row["stars"] is not None and row["stars"] not in range(1, 6):
            raise ValueError(f"Invalid hotel stars: {row['hotel_id']}")
        osm_type, osm_id = match.groups()
        source_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
        if row["osm_url"] != source_url:
            raise ValueError(f"OSM source and identity disagree: {row['hotel_id']}")
        hotels.append(
            {
                "hotel_id": row["hotel_id"],
                "destination": row["destination"],
                "name": row["name"],
                "name_en": row["name_en"],
                "latitude": latitude,
                "longitude": longitude,
                "coordinate_kind": "node"
                if osm_type == "node"
                else "bounding_box_center",
                "stars": row["stars"],
                # S4 saved a normalized snapshot, not original OSM tags or metadata.
                "raw_stars_tag": None,
                "osm_type": osm_type,
                "osm_id": int(osm_id),
                "osm_version": None,
                "osm_timestamp": None,
                "source_url": source_url,
                "distance_from_catchment_center_km": round(
                    distance_km(*ANCHORS[row["destination"]], latitude, longitude), 3
                ),
            }
        )
    counts = Counter(row["destination"] for row in hotels)
    if set(counts) != set(ANCHORS):
        raise ValueError("The shared catalogue must cover AYT, DXB and MLE")
    transfers = json.loads((root / "data/transfers/transfers.json").read_text(encoding="utf-8"))[
        "transfers"
    ]
    pairs = [(row["hotel_id"], row["airport"]) for row in transfers]
    if len(pairs) != len(set(pairs)) or set(pairs) != {
        (row["hotel_id"], row["destination"]) for row in hotels
    }:
        raise ValueError(
            "Shared hotel catalogue and S4 transfer records disagree; rebuild S4 first"
        )
    return {
        directory / "catalog.json": {
            "schema_version": 1,
            "license": "ODbL-1.0",
            "license_url": LICENSE,
            "attribution": ATTRIBUTION,
            "selection": "Exact projection of the shared S4 selection in osm_hotels.json; no independent D3 selection.",
            "limitations": "Normalized OSM reference facts, not verified offers or rules. Original tags, object versions "
            "and timestamps are not retained in this snapshot; unknown metadata stays null.",
            "hotels": sorted(
                hotels, key=lambda row: (row["destination"], row["hotel_id"])
            ),
        },
        directory / "id_aliases.json": {
            "schema_version": 1,
            "note": "Legacy colon IDs for the active shared catalogue only; new outputs use osm:<type>/<id>. "
            "Removed hotels are not redirected to another physical hotel.",
            "aliases": dict(
                sorted(
                    (row["hotel_id"].replace("/", ":"), row["hotel_id"])
                    for row in hotels
                )
            ),
        },
        directory / "sources.json": {
            "schema_version": 2,
            "sources": [
                {
                    "destination": destination,
                    "snapshot_file": "osm_hotels.json",
                    "snapshot_kind": "normalized_osm",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "fetched": snapshot["fetched"],
                    "selected_objects": counts[destination],
                    "license": "ODbL-1.0",
                    "license_url": LICENSE,
                    "attribution": ATTRIBUTION,
                    "metadata_limitations": "Raw stars tags, OSM object versions and timestamps were not retained.",
                }
                for destination in ANCHORS
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-snapshots",
        action="store_true",
        help="Compatibility alias; execution is always offline",
    )
    parser.add_argument(
        "--check", action="store_true", help="Verify generated files without writing"
    )
    args = parser.parse_args()
    outputs = build_outputs()
    for path, data in outputs.items():
        expected = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != expected:
                raise SystemExit(
                    f"Shared hotel projection is stale: {path}; run scripts/fetch_hotels.py"
                )
        else:
            path.write_text(expected)
    counts = Counter(
        row["destination"]
        for row in outputs[ROOT / "data/hotels/catalog.json"]["hotels"]
    )
    print(
        f"{'Checked' if args.check else 'Built'} shared D3/S4 catalogue: {dict(counts)}; network requests: 0"
    )


if __name__ == "__main__":
    main()
