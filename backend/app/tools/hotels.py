"""D3: OSM hotel reference facts plus explicitly synthetic, deterministic offers.

No booking provider or network request is performed at runtime. OSM contributes
only identity/name/coordinates and stars when tagged. Every commercial field is
synthetic and must not be represented as a real hotel's actual policy or price.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.contracts.tools import HotelOffer, HotelSearch, Place

DATA_ROOT = Path(os.environ.get("NA_MALDIVY_DATA_DIR", Path(__file__).resolve().parents[3] / "data"))
SYNTHETIC_NOTICE = (
    "Демонстрационное предложение: цена, наличие, рейтинг, удобства и правила синтетические; "
    "они не описывают фактические условия этого реального отеля."
)
TIMEZONES = {"AYT": "Europe/Istanbul", "DXB": "Asia/Dubai", "MLE": "Indian/Maldives"}


class HotelRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    hotel_id: str
    destination: str
    name: str
    name_en: str | None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    coordinate_kind: str
    stars: int | None = Field(ge=1, le=5)
    raw_stars_tag: str | None
    osm_type: str
    osm_id: int
    osm_version: int | None
    osm_timestamp: str | None
    source_url: str
    distance_from_catchment_center_km: float


class HotelSearchResult(BaseModel):
    """Rich independent output; D1 projection cannot carry all source fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    hotel_id: str
    offer: HotelOffer
    facts: HotelRecord
    synthetic_rules: dict[str, Any]
    rooms: int
    total_nights: int
    price_scope: str = "all_rooms_all_nights_all_travelers_including_synthetic_pet_surcharge"
    attribution: str = "© OpenStreetMap contributors"
    facts_license_url: str = "https://opendatacommons.org/licenses/odbl/1-0/"
    notice: str = SYNTHETIC_NOTICE
    profile: dict[str, Any] | None = None


@lru_cache(maxsize=1)
def _all_hotels() -> tuple[HotelRecord, ...]:
    data = json.loads((DATA_ROOT / "hotels/catalog.json").read_text())
    return tuple(HotelRecord.model_validate(row) for row in data["hotels"])


def load_hotel_catalog(destination: str | None = None) -> list[HotelRecord]:
    """Return OSM facts; an unsupported destination gives no candidates."""
    return [record for record in _all_hotels() if destination is None or record.destination == destination]


@lru_cache(maxsize=1)
def _rules_by_hotel() -> dict[str, dict[str, Any]]:
    data = json.loads((DATA_ROOT / "hotels/synthetic_rules.json").read_text())
    return {row["hotel_id"]: row for row in data["hotels"]}


def get_synthetic_rules(hotel_id: str) -> dict[str, Any]:
    """A copy prevents callers from mutating cached demo policies."""
    return json.loads(json.dumps(_rules_by_hotel()[hotel_id]))


def hotel_id_for_offer(offer_id: str) -> str:
    parts = offer_id.split("/")
    if len(parts) != 3 or parts[0] != "synthetic-hotel" or parts[1] not in _rules_by_hotel():
        raise ValueError("Unknown synthetic hotel offer identifier")
    if len(parts[2]) != 20 or any(c not in "0123456789abcdef" for c in parts[2]):
        raise ValueError("Malformed synthetic hotel offer identifier")
    return parts[1]


def _stable_number(value: str) -> int:
    return int(hashlib.sha256(value.encode()).hexdigest()[:16], 16)


def _available(hotel_id: str, query: HotelSearch) -> bool:
    # A deterministic demo inventory, never a claim of supplier availability.
    return _stable_number(f"inventory-v1:{hotel_id}:{query.check_in}:{query.check_out}") % 29 != 0


def _profile(hotel_id: str) -> dict[str, Any] | None:
    path = DATA_ROOT / "hotel_profiles" / (hotel_id.replace(":", "-") + ".json")
    return json.loads(path.read_text()) if path.exists() else None


async def search_hotel_results(query: HotelSearch) -> list[HotelSearchResult]:
    """Search the saved OSM snapshot and synthetic inventory with hard filters.

    No unavailable/adults-only-with-child/pet-incompatible/nonrefundable-only
    mismatch reaches the caller. Unknown OSM stars do not pass min_stars > 0.
    """
    results = []
    nights = (query.check_out - query.check_in).days
    rooms = math.ceil(query.travelers.count / 2)
    for record in load_hotel_catalog(query.destination):
        rules = get_synthetic_rules(record.hotel_id)
        if query.min_stars and (record.stars is None or record.stars < query.min_stars):
            continue
        if query.travelers.children_ages and rules["adults_only"]:
            continue
        if query.travelers.pets and not rules["pets_allowed"]:
            continue
        if query.refundable_only and not rules["refundable"]:
            continue
        if not _available(record.hotel_id, query):
            continue
        # Entire party, every room and every night; never multiply again.
        seasonal_percent = 90 + ((query.check_in.month * 7 + rules["seed"] % 11) % 5) * 10
        nightly_room_price = rules["base_room_night_rub"] * seasonal_percent // 100
        price = nightly_room_price * rooms * nights + rules["pet_stay_surcharge_rub"] * query.travelers.pets
        if query.max_price_rub is not None and price > query.max_price_rub:
            continue
        fingerprint = json.dumps(
            {"hotel_id": record.hotel_id, "query": query.model_dump(mode="json"), "tariff_version": rules["version"]},
            sort_keys=True,
            separators=(",", ":"),
        )
        offer_id = f"synthetic-hotel/{record.hotel_id}/{hashlib.sha256(fingerprint.encode()).hexdigest()[:20]}"
        offer = HotelOffer(
            id=offer_id,
            name=record.name + " [синтетические условия]",
            place=Place(
                iata=record.destination,
                name=record.name,
                latitude=record.latitude,
                longitude=record.longitude,
                timezone=TIMEZONES[record.destination],
            ),
            check_in=query.check_in,
            check_out=query.check_out,
            price_rub=price,
            stars=record.stars or 0,  # D1 compatibility: 0 means unknown here, not a certified category.
            rating=rules["synthetic_rating"],
            adults_only=rules["adults_only"],
            pets_allowed=rules["pets_allowed"],
            refundable=rules["refundable"],
            check_in_time=time.fromisoformat(rules["check_in_time"]),
            check_out_time=time.fromisoformat(rules["check_out_time"]),
            reception_closes_at=time.fromisoformat(rules["reception_closes_at"])
            if rules["reception_closes_at"]
            else None,
            early_check_in_price_rub=nightly_room_price * rooms // 2 if rules["early_check_in_available"] else None,
            is_synthetic=True,
        )
        results.append(
            HotelSearchResult(
                hotel_id=record.hotel_id,
                offer=offer,
                facts=record,
                synthetic_rules=rules,
                rooms=rooms,
                total_nights=nights,
                profile=_profile(record.hotel_id),
            )
        )
    return sorted(results, key=lambda result: (result.offer.price_rub, result.hotel_id))


async def search_hotels(query: HotelSearch) -> list[HotelOffer]:
    """D1 v0 compatibility projection; use rich results for truthful provenance."""
    return [result.offer for result in await search_hotel_results(query)]


class HotelSearchTool:
    name = "search_hotels"
    description = "Поиск демонстрационных предложений на реальных OSM-точках; коммерческие условия синтетические."
    input_model = HotelSearch
    output_adapter = TypeAdapter(list[HotelOffer])

    async def run(self, input: HotelSearch) -> list[HotelOffer]:
        return await search_hotels(input)
