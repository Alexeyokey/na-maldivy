#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["h5py>=3.12", "pyyaml>=6.0"]
# ///
"""Rebuild D5's offline climate snapshot from NASA POWER and NOAA OISST.

Run from the repository root: uv run scripts/fetch_destination_climate.py
Only this build script needs HDF5/numpy. Runtime suggestions read the committed
snapshot; they never download data. Raw NASA responses are committed for audit,
while the 46 MB NOAA source is cached outside the repository.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import h5py
import numpy as np
import yaml

START_YEAR = 1991
END_YEAR = 2020
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
NOAA_URL = "https://downloads.psl.noaa.gov/Datasets/noaa.oisst.v2.highres/sst.mon.ltm.1991-2020.nc"
NASA_URL = "https://power.larc.nasa.gov/api/temporal/climatology/point"
ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: Any) -> None:
    """Replace a completed JSON file atomically, leaving no half-filled cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_url(url: str) -> bytes:
    """Bounded retries for public source availability and HTTP rate limits."""
    for attempt in range(4):
        try:
            request = Request(url, headers={"User-Agent": "na-maldivy-D5-climate-builder/1.0"})
            with urlopen(request, timeout=60) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == 3:
                raise
        except (URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(2 ** (attempt + 1))
    raise RuntimeError("Unreachable retry state")


def nasa_request_url(seed: dict[str, Any]) -> str:
    location = seed.get("location", seed)
    query = {
        "parameters": "T2M,PRECTOTCORR",
        "community": "AG",
        "longitude": location["longitude"],
        "latitude": location["latitude"],
        "start": START_YEAR,
        "end": END_YEAR,
        "format": "JSON",
    }
    return NASA_URL + "?" + urlencode(query)


def validate_nasa(payload: dict[str, Any]) -> None:
    """Check the returned baseline as well as the requested baseline."""
    period = payload.get("header", {}).get("range", "")
    expected = rf"January\s+{START_YEAR}\s*-\s*December\s+{END_YEAR}"
    if re.search(expected, period) is None:
        raise ValueError(f"NASA returned an unexpected baseline: {period!r}")
    parameters = payload["properties"]["parameter"]
    for variable in ("T2M", "PRECTOTCORR"):
        for month in MONTHS:
            value = parameters[variable][month]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value == -999:
                raise ValueError(f"Missing NASA {variable}/{month}: {value!r}")
    if payload["parameters"]["T2M"]["units"] != "C":
        raise ValueError("Unexpected NASA air temperature units")
    if payload["parameters"]["PRECTOTCORR"]["units"] != "mm/day":
        raise ValueError("Unexpected NASA precipitation units")


def fetch_nasa(seed: dict[str, Any], raw_dir: Path, refresh: bool) -> tuple[str, dict[str, Any]]:
    url = nasa_request_url(seed)
    path = raw_dir / f"{seed['iata']}.json"
    if path.exists() and not refresh:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached["request_url"] != url:
            raise ValueError(f"Cached request changed for {seed['iata']}; use --refresh")
        validate_nasa(cached["response"])
        return seed["iata"], cached
    response = json.loads(read_url(url))
    validate_nasa(response)
    cached = {"request_url": url, "retrieved_at": datetime.now(UTC).date().isoformat(), "response": response}
    write_json(path, cached)
    return seed["iata"], cached


def noaa_file(cache_dir: Path) -> Path:
    path = cache_dir / "sst.mon.ltm.1991-2020.nc"
    if not path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        data = read_url(NOAA_URL)
        if not data.startswith(b"\x89HDF\r\n\x1a\n"):
            raise ValueError("NOAA source is not the expected HDF5/NetCDF4 file")
        temporary = path.with_suffix(".download")
        temporary.write_bytes(data)
        temporary.replace(path)
    return path


def decode_attribute(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


class WaterClimatology:
    def __init__(self, path: Path) -> None:
        with h5py.File(path, "r") as dataset:
            self.latitudes = np.asarray(dataset["lat"][:], dtype=float)
            self.longitudes = (np.asarray(dataset["lon"][:], dtype=float) + 180) % 360 - 180
            self.values = np.asarray(dataset["sst"][:], dtype=float)
            self.counts = np.asarray(dataset["valid_yr_count"][:], dtype=int)
            self.attributes = {key: decode_attribute(value) for key, value in dataset.attrs.items()}
            # HDF5's internal REFERENCE_LIST contains object references, not
            # climate metadata and not JSON-serialisable values.
            time_keys = {
                "units",
                "calendar",
                "standard_name",
                "long_name",
                "delta_t",
                "avg_period",
                "prev_avg_period",
                "climatology",
                "climo_period",
                "ltm_range",
                "actual_range",
            }
            self.time_attributes = {
                key: decode_attribute(value) for key, value in dataset["time"].attrs.items() if key in time_keys
            }
            self.units = decode_attribute(dataset["sst"].attrs["units"])
        if self.values.shape != (12, 720, 1440) or self.units != "degC":
            raise ValueError(f"Unexpected NOAA SST dimensions or units: {self.values.shape}, {self.units}")
        baseline = self.time_attributes.get("climo_period", "")
        if baseline != "1991/01/01 - 2020/12/31":
            raise ValueError(f"Unexpected NOAA climatological period: {baseline!r}")
        self.valid = (
            np.isfinite(self.values).all(axis=0)
            & (self.values >= -3).all(axis=0)
            & (self.values <= 45).all(axis=0)
            & (self.counts >= 27).all(axis=0)
            & (self.counts <= 30).all(axis=0)
        )

    def nearest(self, latitude: float, longitude: float, radius_km: float) -> dict[str, Any] | None:
        """Nearest cell with all 12 SST values and at least 27 years each month."""
        lat_delta = np.radians(self.latitudes[:, None] - latitude)
        lon_delta = np.radians((self.longitudes[None, :] - longitude + 180) % 360 - 180)
        haversine = (
            np.sin(lat_delta / 2) ** 2
            + math.cos(math.radians(latitude))
            * np.cos(np.radians(self.latitudes[:, None]))
            * np.sin(lon_delta / 2) ** 2
        )
        distances = 2 * 6371.0088 * np.arcsin(np.sqrt(np.clip(haversine, 0, 1)))
        distances[~self.valid] = np.inf
        row, column = np.unravel_index(np.argmin(distances), distances.shape)
        distance = float(distances[row, column])
        if not math.isfinite(distance) or distance > radius_km:
            return None
        return {
            "latitude": float(self.latitudes[row]),
            "longitude": float(self.longitudes[column]),
            "distance_km": round(distance, 3),
            "minimum_valid_years": int(self.counts[:, row, column].min()),
            "values_c": [round(float(value), 2) for value in self.values[:, row, column]],
        }


def build(args: argparse.Namespace) -> None:
    input_data = yaml.safe_load(args.input.read_text(encoding="utf-8"))
    seeds = input_data if isinstance(input_data, list) else input_data["destinations"]
    if not seeds or len({seed["iata"] for seed in seeds}) != len(seeds):
        raise ValueError("Seeds must contain unique nonempty IATA records")
    geography = json.loads(args.geography.read_text(encoding="utf-8"))
    airports = {airport["iata"]: airport for airport in geography["airports"]}
    for seed in seeds:
        if "location" not in seed:
            seed["location"] = airports[seed["iata"]]
    raw_dir = args.output.parent / "climate" / "nasa-power"
    nasa = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks = {pool.submit(fetch_nasa, seed, raw_dir, args.refresh): seed["iata"] for seed in seeds}
        for task in as_completed(tasks):
            iata, response = task.result()
            nasa[iata] = response
            print(f"NASA {iata}: verified {START_YEAR}–{END_YEAR}", file=sys.stderr)

    path = noaa_file(args.cache_dir)
    water = WaterClimatology(path)
    snapshot_date = datetime.now(UTC).date().isoformat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    source_note = "Monthly climate normals; not a forecast. Point values represent a grid cell, not beach measurements."
    result = {
        "schema_version": 1,
        "generated_at": snapshot_date,
        "sources": {
            "nasa-power-climatology": {
                "title": "NASA POWER MERRA-2 monthly meteorological climatology",
                "url": "https://power.larc.nasa.gov/docs/services/api/temporal/climatology/",
                "license": "CC BY 4.0",
                "license_url": "https://registry.opendata.aws/nasa-power/",
                "retrieved_at": max(item["retrieved_at"] for item in nasa.values()),
                "period": "1991-2020",
                "method": (
                    "T2M mean air temperature (°C), PRECTOTCORR mean daily precipitation (mm/day), "
                    "requested with start=1991/end=2020 and checked against every returned header.range. "
                    "Monthly precipitation = rounded source daily mean × mean Gregorian month length across 1991–2020."
                ),
            },
            "noaa-oisst-climatology": {
                "title": "NOAA OISST v2.1 0.25° monthly SST climatology",
                "url": NOAA_URL,
                "license": "NOAA data may be used and redistributed for free; acknowledgement requested",
                "license_url": "https://psl.noaa.gov/data/gridded/data.noaa.oisst.v2.highres.html",
                "retrieved_at": snapshot_date,
                "period": "1991-2020",
                "sha256": digest,
                "bytes": path.stat().st_size,
                "method": (
                    f"Nearest valid 0.25° ocean grid cell within {args.water_radius_km:g} km of a beach-tagged "
                    "destination; all 12 months present and ≥27 valid years per month. "
                    "Inland destinations and unavailable ocean cells have null water temperatures."
                ),
                "file_metadata": water.attributes,
                "time_metadata": water.time_attributes,
            },
        },
        "records": {},
    }
    for seed in seeds:
        response = nasa[seed["iata"]]
        parameters = response["response"]["properties"]["parameter"]
        location = seed.get("location", seed)
        cell = None
        if "beach" in seed.get("tags", []):
            cell = water.nearest(location["latitude"], location["longitude"], args.water_radius_km)
        months = []
        for month, name in enumerate(MONTHS, 1):
            mean_days = sum(calendar.monthrange(year, month)[1] for year in range(START_YEAR, END_YEAR + 1)) / 30
            months.append(
                {
                    "month": month,
                    "air_temp_c": parameters["T2M"][name],
                    "water_temp_c": cell["values_c"][month - 1] if cell is not None else None,
                    "precipitation_mm": round(parameters["PRECTOTCORR"][name] * mean_days, 2),
                }
            )
        result["records"][seed["iata"]] = {
            "air_source": "nasa-power-climatology",
            "water_source": "noaa-oisst-climatology" if cell is not None else None,
            "air_request_url": response["request_url"],
            "air_returned_period": response["response"]["header"]["range"],
            "air_raw_file": str((raw_dir / f"{seed['iata']}.json").relative_to(ROOT)),
            "water_cell": {key: value for key, value in cell.items() if key != "values_c"} if cell else None,
            "months": months,
            "notes": source_note,
        }
    write_json(args.output, result)
    water_count = sum(item["water_source"] is not None for item in result["records"].values())
    print(f"Saved {len(seeds)} climate records, {water_count} ocean cells: {args.output}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data" / "destination_seeds.yaml")
    parser.add_argument("--geography", type=Path, default=ROOT / "data" / "sources" / "geography.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "sources" / "climate.json")
    parser.add_argument("--cache-dir", type=Path, default=Path("/tmp/na-maldivy-climate-cache"))
    parser.add_argument("--water-radius-km", type=float, default=100)
    parser.add_argument("--workers", type=int, choices=range(1, 6), default=4)
    parser.add_argument("--refresh", action="store_true", help="Refresh cached NASA point responses")
    args = parser.parse_args()
    if not math.isfinite(args.water_radius_km) or args.water_radius_km <= 0:
        parser.error("--water-radius-km must be a finite positive number")
    build(args)


if __name__ == "__main__":
    main()
