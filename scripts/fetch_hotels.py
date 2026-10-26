#!/usr/bin/env python3
"""Refresh the ODbL OSM hotel snapshot; never used by runtime searches.

Run from the repository root with backend/.venv/bin/python scripts/fetch_hotels.py.
The requests are read-only and contain no credentials. The default keeps all
three saved raw responses, then selects 24 distinct named hotel objects each.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGIONS = {
    "AYT": {
        "latitude": 36.8841,
        "longitude": 30.7056,
        "radius_m": 25000,
        "description": "Antalya urban/coastal hotel catchment; 25 km from city centre.",
    },
    "DXB": {
        "latitude": 25.2048,
        "longitude": 55.2708,
        "radius_m": 20000,
        "bbox": [25.14, 55.20, 25.28, 55.34],
        "description": "Central Dubai bounding box: south25.14 west55.20 north25.28 east55.34; centre is a selection anchor.",
    },
    "MLE": {
        "latitude": 4.1755,
        "longitude": 73.5093,
        "radius_m": 15000,
        "bbox": [4.15, 73.48, 4.25, 73.56],
        "description": "Male/Hulhumale bounding box: south4.15 west73.48 north4.25 east73.56. "
        "This is not a catalogue of all Maldives resorts or a road-only transfer region.",
    },
}
ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
ATTRIBUTION = "© OpenStreetMap contributors"
LICENSE = "https://opendatacommons.org/licenses/odbl/1-0/"


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    a, b = math.radians(lat1), math.radians(lat2)
    h = (
        math.sin((b - a) / 2) ** 2
        + math.cos(a) * math.cos(b) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(h)))


def normalize_snapshot(destination: str, raw: dict, count: int = 24) -> list[dict]:
    region = REGIONS[destination]
    candidates = []
    for element in raw["elements"]:
        tags = element.get("tags", {})
        point = element if element["type"] == "node" else element.get("center", {})
        name = tags.get("name")
        if (
            tags.get("tourism") != "hotel"
            or not name
            or "lat" not in point
            or "lon" not in point
        ):
            continue
        raw_stars = tags.get("stars")
        stars = int(raw_stars) if raw_stars in {"1", "2", "3", "4", "5"} else None
        candidates.append(
            {
                "hotel_id": f"osm:{element['type']}/{element['id']}",
                "destination": destination,
                "name": name,
                "name_en": tags.get("name:en"),
                "latitude": point["lat"],
                "longitude": point["lon"],
                "coordinate_kind": "node"
                if element["type"] == "node"
                else "bounding_box_center",
                "stars": stars,
                "raw_stars_tag": raw_stars,
                "osm_type": element["type"],
                "osm_id": element["id"],
                "osm_version": element.get("version"),
                "osm_timestamp": element.get("timestamp"),
                "source_url": f"https://www.openstreetmap.org/{element['type']}/{element['id']}",
                "distance_from_catchment_center_km": round(
                    distance_km(
                        region["latitude"],
                        region["longitude"],
                        point["lat"],
                        point["lon"],
                    ),
                    3,
                ),
            }
        )
    candidates.sort(
        key=lambda row: (row["distance_from_catchment_center_km"], row["hotel_id"])
    )
    selected = []
    for candidate in candidates:
        duplicate = any(
            candidate["name"].casefold() == previous["name"].casefold()
            and distance_km(
                candidate["latitude"],
                candidate["longitude"],
                previous["latitude"],
                previous["longitude"],
            )
            < 0.15
            for previous in selected
        )
        if not duplicate:
            selected.append(candidate)
        if len(selected) == count:
            break
    if len(selected) < count:
        raise ValueError(
            f"{destination}: only {len(selected)} distinct named hotels; need {count}"
        )
    return selected


def query_for(destination: str) -> str:
    r = REGIONS[destination]
    area = (
        ",".join(str(x) for x in r["bbox"])
        if "bbox" in r
        else f"around:{r['radius_m']},{r['latitude']},{r['longitude']}"
    )
    return f'[out:json][timeout:90];nwr["tourism"="hotel"]["name"]({area});out meta center;'


def fetch(destination: str, endpoint: str) -> tuple[bytes, str, str]:
    query = query_for(destination)
    request_url = endpoint + "?" + urllib.parse.urlencode({"data": query})
    request = urllib.request.Request(
        request_url,
        headers={
            "User-Agent": "na-maldivy-hackathon/0.1 (offline hotel reference snapshot)"
        },
    )
    with urllib.request.urlopen(request, timeout=110) as response:
        content = response.read()
    raw = json.loads(content)
    if "remark" in raw:
        raise ValueError(f"Incomplete Overpass result: {raw['remark']}")
    return content, query, request_url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-snapshots", action="store_true", help="Rebuild without network"
    )
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--destination", choices=tuple(REGIONS), action="append")
    args = parser.parse_args()
    output = ROOT / "data/hotels"
    (output / "osm").mkdir(parents=True, exist_ok=True)
    sources, hotels = [], []
    manifest_path = output / "sources.json"
    previous = (
        json.loads(manifest_path.read_text())
        if manifest_path.exists()
        else {"sources": []}
    )
    previous_by_destination = {
        source["destination"]: source for source in previous["sources"]
    }
    for destination in args.destination or REGIONS:
        raw_path = output / "osm" / f"{destination}.json"
        if args.from_snapshots:
            content = raw_path.read_bytes()
            source = previous_by_destination[destination]
        else:
            errors = []
            for endpoint in ENDPOINTS:
                try:
                    content, query, request_url = fetch(destination, endpoint)
                    break
                except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                    errors.append(f"{endpoint}: {type(exc).__name__}: {exc}")
            else:
                raise RuntimeError("; ".join(errors))
            raw_path.write_bytes(content)
            source = {
                "destination": destination,
                "endpoint": endpoint,
                "request_url": request_url,
                "query": query,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "raw_file": f"osm/{destination}.json",
                "region": REGIONS[destination],
                "license": "ODbL-1.0",
                "license_url": LICENSE,
                "attribution": ATTRIBUTION,
                "copyright_url": "https://www.openstreetmap.org/copyright",
            }
        raw = json.loads(content)
        source["osm_base_timestamp"] = raw.get("osm3s", {}).get("timestamp_osm_base")
        source["returned_objects"] = len(raw["elements"])
        normalized = normalize_snapshot(destination, raw, args.count)
        source["selected_objects"] = len(normalized)
        sources.append(source)
        hotels.extend(normalized)
        print(
            f"{destination}: selected {len(normalized)} of {source['returned_objects']} OSM objects",
            flush=True,
        )
        # Save each manifest immediately so a failed later destination can be resumed.
        merged_sources = dict(previous_by_destination)
        merged_sources.update({s["destination"]: s for s in sources})
        manifest_path.write_text(
            json.dumps(
                {"schema_version": 1, "sources": list(merged_sources.values())},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        if not args.from_snapshots:
            time.sleep(1)
    if args.destination:
        old_path = output / "catalog.json"
        old_hotels = (
            json.loads(old_path.read_text())["hotels"] if old_path.exists() else []
        )
        hotels.extend(h for h in old_hotels if h["destination"] not in args.destination)
    aliases_path = output / "id_aliases.json"
    aliases = (
        json.loads(aliases_path.read_text())["aliases"] if aliases_path.exists() else {}
    )
    aliases.update(
        {
            f"osm:{hotel['osm_type']}:{hotel['osm_id']}": hotel["hotel_id"]
            for hotel in hotels
        }
    )
    aliases_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "note": "Legacy hotel IDs are accepted only as lookup aliases; all new outputs use osm:<type>/<id>.",
                "aliases": dict(sorted(aliases.items())),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    (output / "catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "license": "ODbL-1.0",
                "license_url": LICENSE,
                "attribution": ATTRIBUTION,
                "selection": "Nearest named tourism=hotel objects to each documented region centre, deduplicated by name within 150 m.",
                "limitations": "OSM tags are contributed reference facts, not verified availability, booking offers, ratings or hotel rules.",
                "hotels": sorted(
                    hotels, key=lambda row: (row["destination"], row["hotel_id"])
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
