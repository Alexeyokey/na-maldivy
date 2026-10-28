"""База трансферов аэропорт → отель для трёх демо-направлений: Анталья, Дубай, Мальдивы.

Два шага, запуск из backend/:
  uv run python scripts/build_transfers.py fetch   # нужна сеть: отели и аэропорты из OSM, дороги из OSRM
  uv run python scripts/build_transfers.py build   # без сети: data/transfers/transfers.json по тарифной модели

fetch пишет data/hotels/osm_hotels.json, data/transfers/airports.json и data/transfers/routes_osrm.json.
Эти файлы коммитятся, поэтому build воспроизводим и не зависит от сети. Цены и расписания синтетические,
параметры модели — в TARIFFS ниже. Описание полей и меток — data/transfers/README.md.
"""

import argparse
import hashlib
import json
import math
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
HOTELS_PATH = ROOT / "data" / "hotels" / "osm_hotels.json"
AIRPORTS_PATH = ROOT / "data" / "transfers" / "airports.json"
ROUTES_PATH = ROOT / "data" / "transfers" / "routes_osrm.json"
TRANSFERS_PATH = ROOT / "data" / "transfers" / "transfers.json"

CACHE_DIR = ROOT / "backend" / ".cache" / "build_transfers"  # сырые ответы, в git не попадают

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OSRM_URL = "https://router.project-osrm.org/table/v1/driving/"
HEADERS = {"User-Agent": "na-maldivy-hackathon/0.1 (+https://github.com/ostrovok-hackathon-2026)"}

# Районы: (название, [юг, запад, север, восток], сколько отелей взять). Дальние районы дают ловушку «долгий трансфер».
DESTINATIONS: dict[str, dict[str, Any]] = {
    "AYT": {
        "name": "Анталья",
        "tz": "Europe/Istanbul",
        "airport_bbox": (36.85, 30.75, 36.95, 30.85),
        "areas": [
            ("Анталья и Лара", (36.83, 30.60, 36.92, 30.90), 4),
            ("Белек", (36.83, 31.00, 36.88, 31.15), 4),
            ("Кемер", (36.55, 30.52, 36.66, 30.60), 4),
            ("Сиде", (36.74, 31.35, 36.82, 31.50), 4),
            ("Аланья", (36.50, 31.95, 36.58, 32.10), 4),
        ],
    },
    "DXB": {
        "name": "Дубай",
        "tz": "Asia/Dubai",
        "airport_bbox": (25.22, 55.33, 25.28, 55.40),
        "areas": [
            ("Дейра", (25.25, 55.29, 25.29, 55.34), 4),
            ("Даунтаун", (25.18, 55.26, 25.21, 55.29), 4),
            ("Марина и JBR", (25.06, 55.12, 25.10, 55.15), 4),
            ("Пальма Джумейра", (25.10, 55.11, 25.15, 55.16), 4),
            ("Аль-Барша", (25.09, 55.18, 25.12, 55.21), 4),
        ],
    },
    "MLE": {
        "name": "Мальдивы",
        "tz": "Indian/Maldives",
        "airport_bbox": (4.15, 73.50, 4.23, 73.56),
        "bbox": (-0.8, 72.5, 7.2, 73.8),
        # Такси есть только на островах Мале + Хулхуле и Хулхумале (связаны мостом), остальное — курорты на воде
        "city_boxes": [(4.165, 73.495, 4.200, 73.535), (4.200, 73.535, 4.240, 73.555)],
        # Зоны по расстоянию от аэропорта, км: от них зависит вид трансфера
        "zones": [
            ("Атолл Мале", 0, 60, 5),
            ("Дальние атоллы", 60, 220, 5),
            ("Самые дальние атоллы", 220, 2000, 5),
        ],
    },
}
MLE_CITY = "Мале и Хулхумале"
MIN_GAP_KM = {"MLE": 3.0}  # чтобы курорты не оказались на одном острове; в остальных местах — 0,3 км

# Синтетическая тарифная модель. Цены в рублях за всю машину или за человека, расписание — местное время.
TARIFFS: dict[str, Any] = {
    "AYT": {"traffic": 1.15, "taxi_base": 900, "taxi_km": 70, "taxi_night": 1.3, "shuttle_base": 450, "shuttle_km": 6},
    "DXB": {"traffic": 1.25, "taxi_base": 600, "taxi_km": 55, "taxi_night": 1.0, "metro_price": 180},
    "MLE": {
        "taxi_base": 900,
        "taxi_km": 80,
        "speedboat_base": 5000,
        "speedboat_km": 250,
        "seaplane_base": 20000,
        "seaplane_km": 120,
        "flight_base": 15000,
        "flight_km": 60,
    },
}
NIGHT = {"from": "00:00", "to": "06:00"}
DXB_METRO_AREAS = {"Дейра", "Даунтаун", "Марина и JBR", "Аль-Барша"}


def cached(kind: str, key: str) -> Path:
    return CACHE_DIR / kind / f"{hashlib.sha256(key.encode()).hexdigest()[:16]}.json"


def overpass(query: str) -> list[dict[str, Any]]:
    """Публичные серверы Overpass часто перегружены: повторяем с паузой, чередуем зеркала, ответы кэшируем,
    чтобы повторный запуск не начинал с нуля."""
    path = cached("overpass", query)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["elements"]
    last_error: Exception | None = None
    for attempt in range(8):
        url = OVERPASS_URLS[attempt % len(OVERPASS_URLS)]
        try:
            resp = httpx.post(url, data={"data": query}, headers=HEADERS, timeout=200)
            resp.raise_for_status()
            data = resp.json()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            time.sleep(1)
            return data["elements"]
        except (httpx.HTTPError, ValueError) as e:
            last_error = e
            print(f"  Overpass {url}: {e.__class__.__name__}, повтор через {5 * (attempt + 1)} с", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    sys.exit(f"Overpass не ответил: {last_error}")


def osrm_table(coords: str) -> dict[str, Any]:
    path = cached("osrm", coords)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    resp = httpx.get(
        f"{OSRM_URL}{coords}", params={"sources": "0", "annotations": "duration,distance"}, headers=HEADERS, timeout=60
    )
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, encoding="utf-8")
    return resp.json()


def bbox_str(b: tuple[float, float, float, float]) -> str:
    return ",".join(str(x) for x in b)


def center(e: dict[str, Any]) -> tuple[float, float]:
    point = e if "lat" in e else e["center"]
    return point["lat"], point["lon"]


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def stars(tags: dict[str, str]) -> int | None:
    m = re.match(r"\d", tags.get("stars", ""))
    return int(m.group()) if m else None


def pick(elements: list[dict[str, Any]], n: int, seen: set[str], min_gap_km: float = 0.3) -> list[dict[str, Any]]:
    """Детерминированно: сначала отели со звёздами и сайтом, затем по id. Без повторов названий и не ближе
    min_gap_km друг к другу, чтобы выборка не собралась в одном здании или на одном острове."""

    def rank(e: dict[str, Any]) -> tuple[int, int, str, int]:
        tags = e["tags"]
        return (-int("stars" in tags), -int("website" in tags or "contact:website" in tags), e["type"], e["id"])

    out: list[dict[str, Any]] = []
    for e in sorted(elements, key=rank):
        name = e["tags"]["name"].strip().lower()
        if name in seen or any(haversine_km(center(e), center(o)) < min_gap_km for o in out):
            continue
        seen.add(name)
        out.append(e)
        if len(out) == n:
            break
    return out


def hotel_record(e: dict[str, Any], dest: str, area: str) -> dict[str, Any]:
    lat, lon = center(e)
    tags = e["tags"]
    return {
        "hotel_id": f"osm:{e['type']}/{e['id']}",
        "name": tags["name"],
        "name_en": tags.get("name:en"),
        "destination": dest,
        "area": area,
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "stars": stars(tags),
        "tourism": tags["tourism"],
        "osm_url": f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
    }


def inside(e: dict[str, Any], box: tuple[float, float, float, float]) -> bool:
    lat, lon = center(e)
    south, west, north, east = box
    return south <= lat <= north and west <= lon <= east


def destination_query(d: dict[str, Any]) -> str:
    """Один запрос на направление: аэропорт и отели всех районов. Меньше запросов — меньше шансов упасть
    на перегруженном Overpass."""
    hotel = 'nwr["tourism"~"^(hotel|resort)$"]["name"]'
    boxes = [box for _, box, _ in d["areas"]] if "areas" in d else [d["bbox"]]
    parts = [f'nwr["aeroway"="aerodrome"]["iata"]({bbox_str(d["airport_bbox"])});']
    parts += [f"{hotel}({bbox_str(box)});" for box in boxes]
    return f"[out:json][timeout:180];({''.join(parts)});out center tags;"


def fetch() -> None:
    airports, hotels, routes = [], [], {}
    for code, d in DESTINATIONS.items():
        elements = overpass(destination_query(d))
        found = [e for e in elements if e["tags"].get("iata") == code]
        if not found:
            sys.exit(f"Аэропорт {code} не найден в OSM")
        lat, lon = center(found[0])
        airport = {
            "iata": code,
            "name": found[0]["tags"].get("name:en") or found[0]["tags"].get("name"),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "tz": d["tz"],
            "destination": d["name"],
        }
        airports.append(airport)
        candidates = [e for e in elements if "tourism" in e["tags"]]
        seen: set[str] = set()
        if "areas" in d:
            for area, box, n in d["areas"]:
                in_area = [e for e in candidates if inside(e, box)]
                chosen = pick(in_area, n, seen)
                print(f"{code} {area}: найдено {len(in_area)}, взято {len(chosen)}")
                hotels += [hotel_record(e, code, area) for e in chosen]
            road = [h for h in hotels if h["destination"] == code]
            table = osrm_table(";".join(f"{p['lon']},{p['lat']}" for p in [airport, *road]))
            for i, h in enumerate(road, start=1):
                duration, distance = table["durations"][0][i], table["distances"][0][i]
                if duration is None:
                    sys.exit(f"OSRM не нашёл дорогу до {h['hotel_id']} {h['name']}")
                routes[h["hotel_id"]] = {"airport": code, "duration_s": round(duration), "distance_m": round(distance)}
        else:
            in_city = [e for e in candidates if any(inside(e, box) for box in d["city_boxes"])]
            chosen = pick(in_city, 5, seen)
            print(f"{code} {MLE_CITY}: найдено {len(in_city)}, взято {len(chosen)}")
            hotels += [hotel_record(e, code, MLE_CITY) for e in chosen]
            resorts = [e for e in candidates if e not in in_city]
            for zone, km_from, km_to, n in d["zones"]:
                in_zone = [e for e in resorts if km_from <= haversine_km((lat, lon), center(e)) < km_to]
                chosen = pick(in_zone, n, seen, MIN_GAP_KM[code])
                print(f"{code} {zone}: найдено {len(in_zone)}, взято {len(chosen)}")
                hotels += [hotel_record(e, code, zone) for e in chosen]

    meta = {"source": "© участники OpenStreetMap, ODbL", "fetched": date.today().isoformat()}
    write(HOTELS_PATH, {**meta, "hotels": hotels})
    write(AIRPORTS_PATH, {**meta, "airports": airports})
    write(
        ROUTES_PATH,
        {
            "source": "OSRM, профиль driving, данные © участники OpenStreetMap",
            "fetched": meta["fetched"],
            "routes": routes,
        },
    )
    print(f"Отелей: {len(hotels)}, аэропортов: {len(airports)}, маршрутов по дорогам: {len(routes)}")


def round_to(x: float, step: int) -> int:
    return int(round(x / step) * step)


def stable_bucket(key: str, n: int) -> int:
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % n


def option(hotel: dict[str, Any], airport: str, mode: str, **fields: Any) -> dict[str, Any]:
    opt = {
        "id": f"{hotel['hotel_id']}:{airport}:{mode}",
        "mode": mode,
        "duration_min": fields.pop("duration_min"),
        "distance_km": round(fields.pop("distance_km"), 1),
        "pricing": fields.pop("pricing"),
        "hours": fields.pop("hours", None),
        "luggage_limit": fields.pop("luggage_limit", None),
        "child_seat": fields.pop("child_seat", None),
        "pets": fields.pop("pets", None),
        "prebook_hours": fields.pop("prebook_hours", 0),
        "source": fields.pop("source"),
    }
    assert not fields, f"лишние поля: {fields}"
    opt["labels"] = option_labels(opt)
    return opt


def per_vehicle(price: int, capacity: int, night_multiplier: float = 1.0) -> dict[str, Any]:
    pricing: dict[str, Any] = {"type": "per_vehicle", "price_rub": price, "capacity": capacity}
    if night_multiplier > 1:
        pricing["night_multiplier"] = night_multiplier
        pricing["night_hours"] = NIGHT
    return pricing


def per_person(
    price: int, child_share: float = 0.5, night_multiplier: float = 1.0, night_hours: dict[str, str] | None = None
) -> dict[str, Any]:
    pricing: dict[str, Any] = {
        "type": "per_person",
        "price_rub": price,
        "child_price_rub": round_to(price * child_share, 50),
    }
    if night_multiplier > 1:
        pricing["night_multiplier"] = night_multiplier
        pricing["night_hours"] = night_hours or NIGHT
    return pricing


def option_labels(opt: dict[str, Any]) -> list[str]:
    p = opt["pricing"]
    checks = {
        "long_transfer": opt["duration_min"] > 90,
        "no_night_service": opt["hours"] is not None,
        "daylight_only": opt["mode"] == "seaplane",
        "luggage_limit": opt["luggage_limit"] is not None,
        "prebook_required": opt["prebook_hours"] >= 24,
        "no_child_seat": opt["child_seat"] is False,
        "pets_forbidden": opt["pets"] is False,
        "small_capacity": p["type"] == "per_vehicle" and p["capacity"] <= 4,
        "night_surcharge": p.get("night_multiplier", 1) > 1,
        "multi_leg": opt["mode"] == "flight_speedboat",
    }
    return [label for label, hit in checks.items() if hit]


def road_options(hotel: dict[str, Any], route: dict[str, Any]) -> list[dict[str, Any]]:
    code, t = hotel["destination"], TARIFFS[hotel["destination"]]
    km, drive_min = route["distance_m"] / 1000, route["duration_s"] / 60
    taxi_min = round(drive_min * t["traffic"]) + 10
    taxi_price = round_to(t["taxi_base"] + km * t["taxi_km"], 50)
    opts = [
        option(
            hotel,
            code,
            "taxi",
            duration_min=taxi_min,
            distance_km=km,
            source="osrm",
            pricing=per_vehicle(taxi_price, 4, t["taxi_night"]),
        ),
        option(
            hotel,
            code,
            "private_minivan",
            duration_min=taxi_min,
            distance_km=km,
            source="osrm",
            pricing=per_vehicle(round_to(taxi_price * 1.6, 50), 7),
            child_seat=True,
            pets=True,
            prebook_hours=24,
        ),
    ]
    if code == "AYT":
        opts.append(
            option(
                hotel,
                code,
                "shuttle",
                duration_min=round(drive_min * 1.4) + 30,
                distance_km=km,
                source="osrm",
                pricing=per_person(round_to(t["shuttle_base"] + km * t["shuttle_km"], 50)),
                hours={"from": "06:00", "to": "23:00"},
                pets=False,
            )
        )
    if code == "DXB" and hotel["area"] in DXB_METRO_AREAS:
        opts.append(
            option(
                hotel,
                code,
                "metro",
                duration_min=round(km / 35 * 60) + 20,
                distance_km=km,
                source="synthetic",
                pricing=per_person(t["metro_price"], child_share=0.5),
                hours={"from": "05:00", "to": "00:00"},
                luggage_limit="2 места багажа на человека",
                pets=False,
            )
        )
    return opts


def island_options(hotel: dict[str, Any], airport: dict[str, Any]) -> list[dict[str, Any]]:
    t, hid = TARIFFS["MLE"], hotel["hotel_id"]
    km = haversine_km((airport["lat"], airport["lon"]), (hotel["lat"], hotel["lon"]))
    if hotel["area"] == MLE_CITY:
        return [
            option(
                hotel,
                "MLE",
                "taxi",
                duration_min=round(km * 1.6 / 25 * 60) + 15,
                distance_km=km,
                source="synthetic",
                pricing=per_vehicle(round_to(t["taxi_base"] + km * t["taxi_km"], 50), 4, 1.2),
            )
        ]
    if km < 60:
        # Каждый третий курорт не возит катером ночью, остальные возят с ночной наценкой
        limited = stable_bucket(hid, 3) == 0
        night = {"from": "22:00", "to": "06:00"}
        return [
            option(
                hotel,
                "MLE",
                "speedboat",
                duration_min=round(km / 45 * 60) + 20,
                distance_km=km,
                source="synthetic",
                hours={"from": "06:00", "to": "22:00"} if limited else None,
                pricing=per_person(
                    round_to(t["speedboat_base"] + km * t["speedboat_km"], 100),
                    night_multiplier=1.0 if limited else 1.5,
                    night_hours=night,
                ),
                pets=False,
            )
        ]
    if km < 220:
        return [
            option(
                hotel,
                "MLE",
                "seaplane",
                duration_min=round(km / 150 * 60) + 45,
                distance_km=km,
                source="synthetic",
                hours={"from": "06:00", "to": "15:30"},
                pricing=per_person(round_to(t["seaplane_base"] + km * t["seaplane_km"], 500)),
                luggage_limit="20 кг багажа и 5 кг ручной клади на человека",
                pets=False,
                prebook_hours=72,
            )
        ]
    return [
        option(
            hotel,
            "MLE",
            "flight_speedboat",
            duration_min=round(km / 400 * 60) + 90,
            distance_km=km,
            source="synthetic",
            hours={"from": "06:00", "to": "21:00"},
            pricing=per_person(round_to(t["flight_base"] + km * t["flight_km"], 500)),
            luggage_limit="20 кг багажа на человека",
            pets=False,
            prebook_hours=48,
        )
    ]


def hotel_labels(options: list[dict[str, Any]]) -> list[str]:
    labels = []
    if all(o["hours"] is not None for o in options):
        labels.append("only_scheduled_transfer")
    if min(o["duration_min"] for o in options) > 90:
        labels.append("far_from_airport")
    return labels


def build() -> None:
    hotels = read(HOTELS_PATH)["hotels"]
    airports = {a["iata"]: a for a in read(AIRPORTS_PATH)["airports"]}
    routes = read(ROUTES_PATH)["routes"]
    hotel_ids = [hotel["hotel_id"] for hotel in hotels]
    if len(set(hotel_ids)) != len(hotel_ids):
        raise ValueError("Каталог содержит повторяющиеся hotel_id")
    if unknown_routes := routes.keys() - set(hotel_ids):
        raise ValueError(f"Маршруты ссылаются на отели вне каталога: {sorted(unknown_routes)}")
    records = []
    for h in hotels:
        code, hotel_id = h["destination"], h["hotel_id"]
        if code not in DESTINATIONS or code not in airports:
            raise ValueError(f"Нет поддерживаемого аэропорта {code} для {hotel_id}")
        airport = airports[code]
        if code in {"AYT", "DXB"}:
            route = routes.get(hotel_id)
            if route is None:
                raise ValueError(f"Нет дорожного маршрута OSRM {code} → {hotel_id}; островная модель неприменима")
            if route.get("airport") != code:
                raise ValueError(f"Маршрут {hotel_id} относится к аэропорту {route.get('airport')}, нужен {code}")
            options = road_options(h, route)
        elif hotel_id in routes:
            raise ValueError(f"Для MLE используется островная модель, дорожный маршрут {hotel_id} неприменим")
        else:
            options = island_options(h, airport)
        records.append(
            {
                "hotel_id": h["hotel_id"],
                "airport": airport["iata"],
                "tz": airport["tz"],
                "labels": hotel_labels(options),
                "options": options,
            }
        )
    write(
        TRANSFERS_PATH,
        {
            "note": "Синтетическая база: время по дорогам из OSRM или по расстоянию, цены и расписания — модель из "
            "backend/scripts/build_transfers.py. Описание — data/transfers/README.md.",
            "transfers": records,
        },
    )
    labels: dict[str, int] = {}
    for r in records:
        for label in r["labels"] + [lb for o in r["options"] for lb in o["labels"]]:
            labels[label] = labels.get(label, 0) + 1
    print(f"Отелей: {len(records)}, вариантов: {sum(len(r['options']) for r in records)}")
    print("Метки:", ", ".join(f"{k}={v}" for k, v in sorted(labels.items())))


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["fetch", "build"])
    {"fetch": fetch, "build": build}[parser.parse_args().step]()
