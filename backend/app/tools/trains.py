"""D6: reproducible demo trains and a cached Yandex Timetables adapter.

Yandex supplies a timetable, while price is always our disclosed synthetic model.
The D1 bool is_synthetic therefore stays true even with real timetable records.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from app.contracts.tools import Place, Train, TrainSearch
from app.tools.http_cache import CachedHttpClient
from app.tools.transport import (
    DATA_DIR,
    ProviderNotConfigured,
    ProviderResponseError,
    arrival_after,
    party_id,
    provider_datetime,
    stable_number,
)

MODEL_PATH = DATA_DIR / "trains" / "model.json"
YANDEX_URL = "https://api.rasp.yandex-net.ru/v3.0/search/"


@lru_cache(maxsize=8)
def _model(path: Path = MODEL_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _city(code: str) -> str:
    return "MOW" if code == "SVO" else code


def _point(code: str) -> Place:
    record = _model()["cities"][_city(code)]
    return Place.model_validate({key: record[key] for key in Place.model_fields})


def train_price(query: TrainSearch, *, distance_km: int | None = None, seed: int = 42) -> int:
    """Transparent all-party synthetic tariff; no provider price is invented."""
    model = _model()
    distance = model["route_distance_km"] if distance_km is None else distance_km
    if isinstance(distance, bool) or not isinstance(distance, int) or distance <= 0:
        raise ValueError("distance_km must be a positive integer")
    month, day = query.departure_date.month, query.departure_date.day
    if (month == 12 and day >= 25) or (month == 1 and day <= 10):
        season = model["new_year_multiplier_per_mille"]
    elif month in (6, 7, 8):
        season = model["summer_multiplier_per_mille"]
    else:
        season = model["normal_multiplier_per_mille"]
    # Exclude month here so seasonal comparisons isolate the explicit multiplier.
    variation = 950 + stable_number(seed, _city(query.origin), _city(query.destination)) % 101
    single = distance * model["fare_rub_per_km"][query.seat_class] * season * variation // 1_000_000
    return single * query.travelers.count + model["pet_fee_rub"] * query.travelers.pets


def generate_trains(query: TrainSearch, *, seed: int = 42) -> list[Train]:
    origin_code, destination_code = _city(query.origin), _city(query.destination)
    if {origin_code, destination_code} != {"MOW", "LED"}:
        return []
    origin, destination = _point(origin_code), _point(destination_code)
    variants = (("NIGHT", time(23, 55), 480),)
    if query.seat_class == "seated":
        variants = (("DAY", time(9), 240), *variants)
    trains = []
    for label, departure_time, duration in variants:
        departure = datetime.combine(query.departure_date, departure_time, ZoneInfo(origin.timezone))
        try:
            arrival = arrival_after(departure, duration, destination)
        except OverflowError:
            continue
        trains.append(
            Train(
                id=f"synthetic-train-{origin_code}-{destination_code}-{query.departure_date}"
                f"-{query.seat_class}-{label}-{party_id(query.travelers)}-s{seed}",
                origin=origin.model_copy(deep=True),
                destination=destination.model_copy(deep=True),
                departure_at=departure,
                arrival_at=arrival,
                price_rub=train_price(query, seed=seed),
                number=f"DEMO-{label}",
                seat_class=query.seat_class,
                is_synthetic=True,
            )
        )
    return trains


class YandexTimetableClient:
    def __init__(self, http: CachedHttpClient, key: str | None = None, *, max_pages: int = 20):
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        self.http, self.key, self.max_pages = http, key, max_pages

    async def segments(self, query: TrainSearch) -> list[dict[str, Any]]:
        origin_code, destination_code = _city(query.origin), _city(query.destination)
        if {origin_code, destination_code} != {"MOW", "LED"}:
            return []
        if not self.key and self.http.mode != "replay":
            raise ProviderNotConfigured("Yandex Timetables key is required in live/record mode")
        records = _model()["cities"]
        segments, offset = [], 0
        for _ in range(self.max_pages):
            response = await self.http.get(
                YANDEX_URL,
                params={
                    "from": records[origin_code]["yandex_city_code"],
                    "to": records[destination_code]["yandex_city_code"],
                    "date": query.departure_date.isoformat(),
                    "transport_types": "train",
                    "format": "json",
                    "lang": "ru_RU",
                    "system": "yandex",
                    "transfers": "false",
                    "limit": 100,
                    "offset": offset,
                },
                headers={"Authorization": self.key} if self.key else None,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("error"):
                raise ProviderResponseError("Yandex returned an unsuccessful or malformed timetable")
            if not isinstance(payload.get("segments"), list):
                raise ProviderResponseError("Yandex returned an unsuccessful or malformed timetable")
            page = payload["segments"]
            segments.extend(page)
            pagination = payload.get("pagination", {})
            total = pagination.get("total")
            if total is None:
                raise ProviderResponseError("Yandex response is missing pagination.total")
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                raise ProviderResponseError("Invalid Yandex pagination.total")
            offset += len(page)
            if offset >= total:
                return segments
            if not page:
                raise ProviderResponseError("Yandex pagination made no progress")
        raise ProviderResponseError("Yandex pagination exceeded max_pages; results would be incomplete")


@dataclass(frozen=True)
class TrainSearchResult:
    trains: tuple[Train, ...]
    provenance: tuple[dict[str, Any], ...]


class TrainTool:
    name = "search_trains"
    description = "Поезда Москва–Петербург: расписание демонстрационное либо Яндекс; тариф всегда модельный."
    input_model = TrainSearch
    output_adapter = TypeAdapter(list[Train])

    def __init__(
        self,
        *,
        mode: Literal["synthetic", "provider"] = "synthetic",
        seed: int = 42,
        http: CachedHttpClient | None = None,
        key: str | None = None,
    ):
        if mode not in ("synthetic", "provider"):
            raise ValueError("Unknown train mode")
        self.mode, self.seed = mode, seed
        self.provider = YandexTimetableClient(http, key) if http is not None else None

    async def search_with_provenance(self, query: TrainSearch) -> TrainSearchResult:
        if self.mode == "synthetic":
            trains = generate_trains(query, seed=self.seed)
            return TrainSearchResult(
                tuple(trains),
                tuple(
                    {
                        "offer_id": item.id,
                        "schedule": "synthetic",
                        "price": "synthetic distance/class/season/group model",
                        "model_file": "data/trains/model.json",
                        "seed": self.seed,
                    }
                    for item in trains
                ),
            )
        if self.provider is None:
            raise ProviderNotConfigured("A CachedHttpClient is required for provider trains")
        segments = await self.provider.segments(query)
        if not segments:
            return TrainSearchResult((), ())
        origin, destination = _point(query.origin), _point(query.destination)
        records = _model()["cities"]
        origin_station = records[_city(query.origin)]["station_code"]
        destination_station = records[_city(query.destination)]["station_code"]
        trains, provenance = [], []
        for segment in segments:
            if segment.get("has_transfers") or segment.get("thread", {}).get("transport_type") != "train":
                continue
            thread = segment["thread"]
            vehicle = f"{thread.get('vehicle', '')} {thread.get('title', '')}".casefold()
            if query.seat_class != "seated" and any(name in vehicle for name in ("сапсан", "sapsan", "ласточка")):
                continue
            # D1 Place has no provider station id. Limit this first adapter to the
            # two mapped main stations instead of claiming coordinates for others.
            if segment.get("from", {}).get("code") != origin_station:
                continue
            if segment.get("to", {}).get("code") != destination_station:
                continue
            try:
                departure = provider_datetime(segment["departure"], origin)
                arrival = provider_datetime(segment["arrival"], destination)
                number = segment["thread"]["number"]
                uid = segment["thread"]["uid"]
            except (KeyError, TypeError) as exc:
                raise ProviderResponseError("Incomplete Yandex train segment") from exc
            elapsed = (arrival.astimezone(UTC) - departure.astimezone(UTC)).total_seconds()
            if elapsed <= 0 or abs(elapsed - float(segment.get("duration", -1))) > 1:
                raise ProviderResponseError("Yandex duration disagrees with offset-aware timestamps")
            if departure.date() != query.departure_date:
                continue
            train = Train(
                id=f"provider-mixed-train-"
                f"{stable_number(uid, departure.isoformat(), query.model_dump(mode='json'), self.seed):x}",
                origin=origin.model_copy(deep=True),
                destination=destination.model_copy(deep=True),
                departure_at=departure,
                arrival_at=arrival,
                number=number,
                price_rub=train_price(query, seed=self.seed),
                seat_class=query.seat_class,
                is_synthetic=True,
            )
            trains.append(train)
            provenance.append(
                {
                    "offer_id": train.id,
                    "provider": "Yandex Timetables",
                    "provider_url": YANDEX_URL,
                    "schedule": "provider timetable",
                    "thread_uid": uid,
                    "from_station": origin_station,
                    "to_station": destination_station,
                    "price": "synthetic distance/class/season/group model; tickets_info is not used",
                    "class_availability": "unknown; requested class is a modelling input, not confirmed inventory",
                    "model_file": "data/trains/model.json",
                    "seed": self.seed,
                }
            )
        return TrainSearchResult(tuple(trains), tuple(provenance))

    async def run(self, input: TrainSearch) -> list[Train]:
        return list((await self.search_with_provenance(input)).trains)
