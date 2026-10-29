"""Deterministic supplemental demo transport; no observed service is invented.

The checked-in source snapshots remain independent. Only explicit demo adapters
load these supplements. Coordinates anchor fictional hotels and illustrative
nonstop itineraries; they do not establish real road or airline availability.
"""

import json
import math
from pathlib import Path

from app.tools.destinations import load_catalogue

GENERATOR = "deterministic-demo-transport-v1"
NOTICE = (
    "ДЕМО-МОК: расписание, прямота маршрута, тариф и наземный путь рассчитаны синтетически; "
    "это не котировка, не OSRM и не подтверждение существования рейса или услуги."
)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance, used only as a disclosed synthetic input."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = phi2 - phi1, math.radians(lon2 - lon1)
    term = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1, max(0, term))))


def build_routes(data_dir: Path) -> dict:
    catalogue = load_catalogue(data_dir / "destinations.yaml")
    original = json.loads((data_dir / "routes/network.json").read_text(encoding="utf-8"))
    airports = {key: value for key, value in original["airports"].items() if key in {"SVO", "LED"}}
    for row in catalogue.destinations:
        airports.setdefault(
            row.iata,
            {
                "iata": row.iata,
                "name": row.name,
                **row.location.model_dump(),
                "kind": "airport",
                "id": "airport:" + row.iata,
            },
        )
    observed = {(row["origin"], row["destination"]) for row in original["routes"]}
    routes = []
    for origin, estimate_origin in (("SVO", "MOW"), ("LED", "LED")):
        start = airports[origin]
        for destination in catalogue.destinations:
            if origin == destination.iata or (origin, destination.iata) in observed:
                continue
            km = distance_km(
                start["latitude"], start["longitude"], destination.location.latitude, destination.location.longitude
            )
            estimate = getattr(destination.flight_time_estimates, estimate_origin)
            duration = estimate.minutes if estimate else max(60, math.ceil(km / 750 * 60 + 50))
            # A per-seat, one-way model independent of any provider quote.
            price = max(4000, min(75000, round((3500 + km * 3.1) / 100) * 100))
            routes.append(
                {
                    "origin": origin,
                    "destination": destination.iata,
                    "stops": 0,
                    "via": [],
                    "sources": ["deterministic_mock"],
                    "demo_source": "deterministic_mock",
                    "synthetic_duration_minutes": duration,
                    "synthetic_base_price_rub": price,
                }
            )
    return {
        "schema_version": 1,
        "generator": GENERATOR,
        "demo_source": "deterministic_mock",
        "notice": NOTICE,
        "sources": {
            "deterministic_mock": {
                "method": "D5 airport coordinates/timezones and flight-time estimates; illustrative nonstop topology; "
                "otherwise distance / 750 km/h + 50 min. "
                "Seat fare: max(4000, min(75000, round((3500 + km*3.1)/100)*100)).",
                "availability": "not checked; topology and prices are fictional demo assumptions",
            }
        },
        "airports": airports,
        "routes": routes,
        "synthetic_policy": {
            "reverse_routes": "The demo reverses each modelled route; no reverse service is independently confirmed.",
            "calendar": "Every calendar date is modelled, including dates after the recorded quote snapshot.",
        },
    }


def build_transfers(data_dir: Path) -> dict:
    catalogue = load_catalogue(data_dir / "destinations.yaml")
    destinations = {row.iata: row for row in catalogue.destinations}
    hotels = json.loads((data_dir / "demo/hotels.generated.json").read_text(encoding="utf-8"))["hotels"]
    transfers, seen = [], set()
    for hotel in hotels:
        hotel_id, airport = hotel["hotel_id"], hotel["destination"]
        if hotel_id in seen or not hotel_id.startswith(f"demo:hotel/{airport}-"):
            raise ValueError("Generated hotel IDs must be unique and use their destination's demo namespace")
        seen.add(hotel_id)
        destination = destinations[airport]
        if hotel["timezone"] != destination.location.timezone:
            raise ValueError("Generated hotel timezone does not match D5")
        km = round(
            distance_km(
                destination.location.latitude,
                destination.location.longitude,
                hotel["latitude"],
                hotel["longitude"],
            )
            * 1.35
            + 2,
            1,
        )
        duration = max(10, math.ceil(km / 35 * 60 + 8))
        taxi_price = max(500, math.ceil((200 + km * 65) / 100) * 100)
        options = []
        for mode in ("taxi", "private_minivan", "shuttle"):
            minivan, shuttle = mode == "private_minivan", mode == "shuttle"
            options.append(
                {
                    "id": f"{hotel_id}:{airport}:{mode}",
                    "mode": mode,
                    "duration_min": duration + (20 if shuttle else 0),
                    "distance_km": km,
                    "pricing": (
                        {
                            "type": "per_person",
                            "price_rub": max(250, math.ceil(taxi_price * 0.3 / 50) * 50),
                            "child_price_rub": max(150, math.ceil(taxi_price * 0.15 / 50) * 50),
                        }
                        if shuttle
                        else {
                            "type": "per_vehicle",
                            "price_rub": math.ceil(taxi_price * (1.45 if minivan else 1) / 100) * 100,
                            "capacity": 7 if minivan else 4,
                        }
                    ),
                    "hours": {"from": "06:00", "to": "23:00"} if shuttle else None,
                    "luggage_limit": None,
                    "child_seat": True if minivan else None,
                    "pets": True if minivan else (False if shuttle else None),
                    "prebook_hours": 0,
                    "source": "synthetic",
                    "labels": ["deterministic_mock", "synthetic_distance_not_osrm"]
                    + (["pets_forbidden", "no_night_service"] if shuttle else []),
                }
            )
        transfers.append(
            {
                "hotel_id": hotel_id,
                "airport": airport,
                "tz": destination.location.timezone,
                "labels": ["deterministic_mock", "fictional_demo_hotel"],
                "options": options,
            }
        )
    return {
        "schema_version": 1,
        "generator": GENERATOR,
        "demo_source": "deterministic_mock",
        "notice": NOTICE,
        "distance_method": "haversine(D5 airport, fictional hotel) * 1.35 + 2 km; 35 km/h + 8 min; shuttle +20 min",
        "group_policy": "Vehicle capacity counts adults and all children, including infants; extra vehicles multiply "
        "the fare. Shuttle infants under 2 are free, children 2–11 pay child price, 12+ adult price. Minivans "
        "illustratively include child seats, pets and luggage without extra charges; no real service is promised.",
        "transfers": transfers,
    }


def canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
