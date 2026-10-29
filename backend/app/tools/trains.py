"""D6: reproducible demo trains and a cached Yandex Timetables adapter.

Yandex supplies a timetable, while price is always our disclosed synthetic model.
The D1 bool is_synthetic therefore stays true even with real timetable records.
"""

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import httpx
from pydantic import AwareDatetime, TypeAdapter, ValidationError

from app.contracts.tools import Place, Train, TrainSearch
from app.data_paths import resolve_data_dir
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
PRICING_VERSION = "train-model-v2"
YANDEX_URL = "https://api.rasp.yandex-net.ru/v3.0/search/"


@lru_cache(maxsize=8)
def _model(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _city(code: str) -> str:
    return "MOW" if code == "SVO" else code


def _point(code: str, path: Path) -> Place:
    record = _model(path)["cities"][_city(code)]
    return Place.model_validate({key: record[key] for key in Place.model_fields})


def train_price(
    query: TrainSearch,
    *,
    distance_km: int | None = None,
    seed: int = 42,
    train_type: Literal["standard", "fast", "branded"] = "standard",
    model_path: Path | None = None,
) -> int:
    """Transparent all-party synthetic tariff; no provider price is invented."""
    model = _model(model_path or resolve_data_dir() / "trains/model.json")
    distance = model["route_distance_km"] if distance_km is None else distance_km
    if isinstance(distance, bool) or not isinstance(distance, int) or distance <= 0:
        raise ValueError("distance_km must be a positive integer")
    if train_type not in model["train_type_multiplier_per_mille"]:
        raise ValueError("Unknown synthetic train type")
    train_factor = model["train_type_multiplier_per_mille"][train_type]
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
    single = single * train_factor // 1000
    return single * query.travelers.count + model["pet_fee_rub"] * query.travelers.pets


def generate_trains(query: TrainSearch, *, seed: int = 42, model_path: Path | None = None) -> list[Train]:
    origin_code, destination_code = _city(query.origin), _city(query.destination)
    if {origin_code, destination_code} != {"MOW", "LED"}:
        return []
    model_path = model_path or resolve_data_dir() / "trains/model.json"
    origin, destination = _point(origin_code, model_path), _point(destination_code, model_path)
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
                f"-{query.seat_class}-{label}-{party_id(query.travelers)}-s{seed}-{PRICING_VERSION}",
                origin=origin.model_copy(deep=True),
                destination=destination.model_copy(deep=True),
                departure_at=departure,
                arrival_at=arrival,
                price_rub=train_price(
                    query, seed=seed, train_type="fast" if label == "DAY" else "standard", model_path=model_path
                ),
                number=f"DEMO-{label}",
                seat_class=query.seat_class,
                is_synthetic=True,
                schedule_source="synthetic",
            )
        )
    return trains


class TrainHttpClient(Protocol):
    @property
    def mode(self) -> Literal["record", "replay", "live"]: ...

    async def get(self, url: str, **kwargs: Any) -> httpx.Response: ...


@dataclass(frozen=True)
class TimetableSegment:
    payload: dict[str, Any]
    cache_mode: Literal["record", "replay", "live"]
    recorded_at: datetime | None


class YandexTimetableClient:
    def __init__(
        self, http: TrainHttpClient, key: str | None = None, *, max_pages: int = 20, model_path: Path | None = None
    ):
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        self.http, self.key, self.max_pages = http, key, max_pages
        self.model_path = model_path or resolve_data_dir() / "trains/model.json"

    async def segments(self, query: TrainSearch) -> list[dict[str, Any]]:
        """Keep the existing provider-record API for callers that only need data."""
        return [record.payload for record in await self.segment_records(query)]

    async def segment_records(self, query: TrainSearch) -> list[TimetableSegment]:
        """Attach each response's public cache evidence without shared mutable state."""
        origin_code, destination_code = _city(query.origin), _city(query.destination)
        if {origin_code, destination_code} != {"MOW", "LED"}:
            return []
        if not self.key and self.http.mode != "replay":
            raise ProviderNotConfigured("Yandex Timetables key is required in live/record mode")
        records = _model(self.model_path)["cities"]
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
            try:
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPStatusError, ValueError):
                raise ProviderResponseError("Yandex returned an HTTP error or invalid JSON") from None
            if not isinstance(payload, dict) or payload.get("error"):
                raise ProviderResponseError("Yandex returned an unsuccessful or malformed timetable")
            if not isinstance(payload.get("segments"), list):
                raise ProviderResponseError("Yandex returned an unsuccessful or malformed timetable")
            page = payload["segments"]
            if any(not isinstance(item, dict) for item in page):
                raise ProviderResponseError("Malformed Yandex segment")
            cache_mode = response.extensions.get("cache_mode", self.http.mode)
            if cache_mode not in ("record", "replay", "live") or cache_mode != self.http.mode:
                raise ProviderResponseError("Inconsistent Yandex timetable cache mode")
            try:
                recorded_at = (
                    TypeAdapter(AwareDatetime).validate_python(response.extensions["recorded_at"])
                    if response.extensions.get("recorded_at") is not None
                    else None
                )
            except ValidationError:
                raise ProviderResponseError("Invalid Yandex timetable recording timestamp") from None
            segments.extend(TimetableSegment(item, cache_mode, recorded_at) for item in page)
            pagination = payload.get("pagination", {})
            if not isinstance(pagination, dict):
                raise ProviderResponseError("Malformed Yandex pagination")
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
        http: TrainHttpClient | None = None,
        key: str | None = None,
        data_dir: Path | None = None,
    ):
        if mode not in ("synthetic", "provider"):
            raise ValueError("Unknown train mode")
        self.mode, self.seed = mode, seed
        self.data_dir = resolve_data_dir(data_dir)
        self.model_path = self.data_dir / "trains/model.json"
        self.provider = YandexTimetableClient(http, key, model_path=self.model_path) if http is not None else None

    async def search_with_provenance(self, query: TrainSearch) -> TrainSearchResult:
        if self.mode == "synthetic":
            trains = generate_trains(query, seed=self.seed, model_path=self.model_path)
            return TrainSearchResult(
                tuple(trains),
                tuple(
                    {
                        "offer_id": item.id,
                        "schedule": "synthetic",
                        "price": "synthetic distance/class/season/group model",
                        "model_file": "data/trains/model.json",
                        "seed": self.seed,
                        "pricing_version": PRICING_VERSION,
                    }
                    for item in trains
                ),
            )
        if self.provider is None:
            raise ProviderNotConfigured("An injected HTTP client is required for provider trains")
        segments = await self.provider.segment_records(query)
        if not segments:
            return TrainSearchResult((), ())
        origin, destination = _point(query.origin, self.model_path), _point(query.destination, self.model_path)
        records = _model(self.model_path)["cities"]
        origin_station = records[_city(query.origin)]["station_code"]
        destination_station = records[_city(query.destination)]["station_code"]
        trains, provenance = [], []
        seen = set()
        for record in segments:
            segment = record.payload
            if any(not isinstance(segment.get(key), dict) for key in ("thread", "from", "to")):
                raise ProviderResponseError("Malformed Yandex train segment")
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
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderResponseError("Incomplete Yandex train segment") from exc
            if not isinstance(number, str) or not number.strip() or not isinstance(uid, str) or not uid.strip():
                raise ProviderResponseError("Missing Yandex train identity")
            raw_duration = segment.get("duration")
            if isinstance(raw_duration, bool) or not isinstance(raw_duration, (int, float)):
                raise ProviderResponseError("Invalid Yandex duration")
            duration = float(raw_duration)
            elapsed = (arrival.astimezone(UTC) - departure.astimezone(UTC)).total_seconds()
            if elapsed <= 0 or not math.isfinite(duration) or abs(elapsed - duration) > 1:
                raise ProviderResponseError("Yandex duration disagrees with offset-aware timestamps")
            if departure.date() != query.departure_date:
                continue
            if (uid, departure) in seen:
                continue
            seen.add((uid, departure))
            train_type = "fast" if any(name in vehicle for name in ("сапсан", "sapsan", "ласточка")) else "standard"
            identifier = stable_number(
                uid, departure.isoformat(), query.model_dump(mode="json"), self.seed, PRICING_VERSION
            )
            train = Train(
                id=f"provider-mixed-train-{identifier:x}",
                origin=origin.model_copy(deep=True),
                destination=destination.model_copy(deep=True),
                departure_at=departure,
                arrival_at=arrival,
                number=number,
                price_rub=train_price(query, seed=self.seed, train_type=train_type, model_path=self.model_path),
                seat_class=query.seat_class,
                is_synthetic=True,
                schedule_source="yandex",
                schedule_cache_mode=record.cache_mode,
                schedule_recorded_at=record.recorded_at,
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
                    "pricing_version": PRICING_VERSION,
                }
            )
        return TrainSearchResult(tuple(trains), tuple(provenance))

    async def run(self, input: TrainSearch) -> list[Train]:
        return list((await self.search_with_provenance(input)).trains)

    def public_metadata(self, result: list[Train]) -> dict[str, Any]:
        """Public source boundaries; no headers, key, raw provider payload or fare claim."""
        source = "yandex_timetable" if self.mode == "provider" else "synthetic"
        return {
            "source": source if result else None,
            "configured_source": source,
            "schedule_quote_count": len(result),
            "cache_mode": self.provider.http.mode if self.mode == "provider" and self.provider else None,
            "price_modelled": True,
            "availability_confirmed": False,
            "class_availability_confirmed": False,
            "price_scope": "group_one_way",
        }
