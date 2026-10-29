"""Opt-in fictional hotel data, separate from the original OSM snapshot.

These records describe invented demo hotels, including their coordinates. No
OSM identity, supplier availability or real guest review is attributed to them.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEMO_HOTEL_ID = re.compile(r"demo:hotel/([A-Z]{3})-(0[1-8])\Z")
GENERATOR = "synthetic-demo-hotels-v1"
FICTIONAL_NOTICE = (
    "Вымышленный демонстрационный отель: название, координаты, цена, наличие, рейтинг, "
    "условия и отзывы синтетические. Это не реальный отель и не предложение поставщика."
)


class DemoModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ExpandedHotelRecord(DemoModel):
    hotel_id: str
    destination: str = Field(pattern=r"^[A-Z]{3}$")
    name: str = Field(min_length=1)
    name_en: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str
    coordinate_kind: Literal["synthetic_offset_from_airport"]
    stars: int = Field(strict=True, ge=3, le=5)
    source_kind: Literal["fictional_demo"]
    is_fictional: Literal[True]

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def scoped_identity(self):
        match = DEMO_HOTEL_ID.fullmatch(self.hotel_id)
        if match is None or match[1] != self.destination:
            raise ValueError("Fictional hotel identity must match its destination")
        return self


class ExpandedHotelRules(DemoModel):
    hotel_id: str
    version: Literal["synthetic-demo-hotel-v1"]
    seed: int = Field(strict=True, ge=0)
    is_synthetic: Literal[True]
    scenario: str
    notice: str = Field(min_length=1)
    always_available: bool
    safe_rule: Literal["family_pets_refundable_24h"] | None
    base_room_night_rub: int = Field(strict=True, gt=0)
    room_capacity_people: int = Field(strict=True, ge=1)
    room_capacity_adults: int = Field(strict=True, ge=1)
    infant_max_age: Literal[2]
    adults_only: bool
    pets_allowed: bool
    refundable: bool
    pet_stay_surcharge_rub: int = Field(strict=True, ge=0)
    check_in_time: str
    check_out_time: str
    reception_24h: bool
    reception_closes_at: str | None
    early_check_in_available: bool
    synthetic_airport_transfer_minutes: None
    synthetic_airport_transfer_source: None
    synthetic_beach_distance_m: int = Field(strict=True, ge=0)
    synthetic_rating: float = Field(ge=0, le=10)
    synthetic_amenities: tuple[str, ...]
    pricing_note: str

    @model_validator(mode="after")
    def safe_inventory_has_safe_rules(self):
        if self.reception_24h != (self.reception_closes_at is None):
            raise ValueError("Reception hours disagree with the 24-hour rule")
        if self.safe_rule is not None or self.always_available:
            if not (
                self.safe_rule == "family_pets_refundable_24h"
                and not self.adults_only
                and self.pets_allowed
                and self.refundable
                and self.reception_24h
            ):
                raise ValueError("Safe fictional inventory requires family, pets, refund and 24-hour reception")
        return self


class ExpandedHotelEntry(ExpandedHotelRecord):
    rules: ExpandedHotelRules

    @model_validator(mode="after")
    def rules_match_identity(self):
        if self.rules.hotel_id != self.hotel_id:
            raise ValueError("Fictional hotel facts and rules must share one identity")
        return self


class ExpandedHotelCatalogue(DemoModel):
    schema_version: Literal[1]
    generator: Literal["synthetic-demo-hotels-v1"]
    is_synthetic: Literal[True]
    notice: str
    source_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_method: str
    destinations: tuple[str, ...]
    hotels: tuple[ExpandedHotelEntry, ...]

    @model_validator(mode="after")
    def unique_complete_destinations(self):
        expected = set(self.destinations)
        if not expected or len(expected) != len(self.destinations):
            raise ValueError("Fictional destination list must be nonempty and unique")
        identities = {row.hotel_id for row in self.hotels}
        required = {f"demo:hotel/{code}-{number:02d}" for code in expected for number in range(1, 9)}
        if identities != required or len(identities) != len(self.hotels):
            raise ValueError("Fictional catalogue needs exactly eight unique hotels per destination")
        for code in expected:
            rules = [row.rules for row in self.hotels if row.destination == code]
            safe = [rule for rule in rules if rule.safe_rule is not None]
            if len(safe) < 2 or not any(rule.always_available for rule in safe):
                raise ValueError("Each fictional destination needs safe family alternatives and stable demo inventory")
        return self


@lru_cache(maxsize=8)
def load_expanded_catalogue(data_dir: Path) -> ExpandedHotelCatalogue | None:
    path = data_dir / "demo/hotels.generated.json"
    if not path.exists():
        return None
    return ExpandedHotelCatalogue.model_validate_json(path.read_bytes())


@lru_cache(maxsize=8)
def expanded_hotel_records(data_dir: Path) -> tuple[ExpandedHotelRecord, ...]:
    catalogue = load_expanded_catalogue(data_dir)
    if catalogue is None:
        return ()
    return tuple(ExpandedHotelRecord.model_validate(row.model_dump(exclude={"rules"})) for row in catalogue.hotels)


@lru_cache(maxsize=8)
def expanded_hotel_rules(data_dir: Path) -> dict[str, dict]:
    catalogue = load_expanded_catalogue(data_dir)
    if catalogue is None:
        return {}
    return {row.hotel_id: row.rules.model_dump(mode="json") for row in catalogue.hotels}
