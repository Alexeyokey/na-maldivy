"""Собрать D5-каталог из сохранённых источников; сеть и D1 не нужны."""

import argparse
import json
import math
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def distance_km(first: dict, second: dict) -> float:
    latitude1, latitude2 = map(math.radians, (first["latitude"], second["latitude"]))
    delta_lat = latitude2 - latitude1
    delta_lon = math.radians(second["longitude"] - first["longitude"])
    value = math.sin(delta_lat / 2) ** 2 + math.cos(latitude1) * math.cos(latitude2) * math.sin(delta_lon / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1, math.sqrt(value)))


def build_catalogue() -> dict:
    from app.tools.destinations import Catalogue

    seeds = yaml.safe_load((ROOT / "data/destination_seeds.yaml").read_text(encoding="utf-8"))["destinations"]
    geography = read_json(ROOT / "data/sources/geography.json")
    climate = read_json(ROOT / "data/sources/climate.json")
    visas = read_json(ROOT / "data/sources/visas.json")
    as_of = visas["as_of"]
    airports = {item["iata"]: item for item in geography["airports"]}
    policies = {item["country_code"]: item for item in visas["policies"]}
    geo = geography["source"]
    sources = {
        geo["id"]: {
            "title": geo["title"],
            "url": geo["url"],
            "license": geo["license"],
            "retrieved_at": geo["retrieved_at"],
            "period": None,
            "method": f"Pinned commit {geo['pinned_commit']}; SHA256 {geo['sha256']}. {geo['transformation']}",
        },
        "editorial-classification": {
            "title": "D5: авторские теги и классификация внутренних поездок",
            "url": "https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/21",
            "license": None,
            "retrieved_at": as_of,
            "period": None,
            "method": (
                "Tags are editorial navigation labels, not a guarantee of hotel amenities. "
                "Domestic means destination country RU equals traveller citizenship RU; "
                "special documents and local access restrictions are not assessed."
            ),
        },
    }
    for identifier, source in climate["sources"].items():
        sources[identifier] = {
            key: source[key] for key in ("title", "url", "license", "retrieved_at", "period", "method")
        }
    for policy in policies.values():
        for source in policy["sources"]:
            sources[source["id"]] = {
                "title": source["title"],
                "url": source["url"],
                "license": None,
                "retrieved_at": source["checked_on"],
                "period": None,
                "method": "Manual official-source check for ordinary RU passports and tourism; see visa conditions.",
            }
    records = []
    for seed in seeds:
        airport = airports[seed["iata"]]
        if airport["country"] != seed["country_code"]:
            raise ValueError(f"Country mismatch: {seed['iata']}")
        point = climate["records"][seed["iata"]]
        policy = policies[seed["country_code"]]
        estimates = {}
        for origin, start in geography["origins"].items():
            if start["anchor_iata"] == seed["iata"]:
                estimates[origin] = None
                continue
            distance = distance_km(start, airport)
            estimates[origin] = {
                "minutes": math.ceil((distance / 750 * 60 + 45) / 5) * 5,
                "method": (
                    f"Estimate: great-circle distance {distance:.1f} km from {start['anchor_iata']} / "
                    "750 km/h + 45 min, rounded up to 5 min. Not a timetable; "
                    "does not establish route availability or account for airspace, stops or detours."
                ),
            }
        water_note = "Морская температура не заполнялась: нет пляжного тега или подходящей ячейки."
        if point["water_cell"] is not None:
            cell = point["water_cell"]
            water_note = (
                f"Морская ячейка NOAA: {cell['latitude']}, {cell['longitude']}; "
                f"до аэропорта {cell['distance_km']} км. Не измерение конкретного пляжа."
            )
        visa_source = policy["sources"][0]["id"] if policy["sources"] else None
        if policy["regime"] == "domestic":
            visa_source = "editorial-classification"
        records.append(
            {
                **seed,
                "location": {key: airport[key] for key in ("latitude", "longitude", "timezone")},
                "geo_source": geo["id"],
                "climate": {
                    "air_source": point["air_source"],
                    "water_source": point["water_source"],
                    "months": point["months"],
                    "notes": (
                        "Нормы 1991–2020. Воздух/осадки — ячейка NASA MERRA-2 у аэропорта, "
                        "вода — ближайшая морская ячейка NOAA OISST 0.25°. "
                        "Это месячные средние, не прогноз. " + water_note
                    ),
                },
                "visa": {
                    "citizenship": "RU",
                    "passport_type": "ordinary",
                    "purpose": "tourism",
                    "regime": policy["regime"],
                    "verification_status": policy["verification_status"],
                    "max_stay_days": policy["max_stay_days"],
                    "rolling_window_days": policy["rolling_window_days"],
                    "checked_on": policy["checked_on"],
                    "effective_from": policy["effective_from"],
                    "effective_until": policy["effective_to"],
                    "source": visa_source,
                    "conditions": " ".join(policy["conditions"]),
                },
                "prices": {
                    "round_trip_flight_rub": {"MOW": None, "LED": None},
                    "hotel_night_rub": None,
                    "status": "pending",
                    "source": None,
                    "notes": "Ожидает источники D2/D3. null означает неизвестную цену, а не бесплатную поездку.",
                },
                "flight_time_estimates": estimates,
            }
        )
    result = {"schema_version": 1, "as_of": as_of, "sources": sources, "destinations": records}
    Catalogue.model_validate(result)
    return result


def catalogue_yaml() -> str:
    return yaml.safe_dump(build_catalogue(), allow_unicode=True, sort_keys=False, width=110)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Проверить актуальность сохранённой выгрузки")
    parser.add_argument("--output", type=Path, default=ROOT / "data/destinations.yaml")
    args = parser.parse_args()
    content = catalogue_yaml()
    if args.check:
        if args.output.read_text(encoding="utf-8") != content:
            raise SystemExit("Справочник устарел: make destinations-data")
        print("Справочник актуален: 48 направлений")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
        print(args.output)


if __name__ == "__main__":
    main()
