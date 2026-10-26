"""Трансферы аэропорт → отель по базе data/transfers/transfers.json (S4, #16).

Модели временные: когда контракты D1 (#2) попадут в main, quote_transfers станет реализацией get_transfer.
Как устроена база и что значат метки — data/transfers/README.md.
"""

import json
import math
import os
from collections.abc import Iterator
from datetime import datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

# backend/app/tools/transfers.py → корень репозитория; в docker это /data (см. docker-compose.yml)
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "transfers" / "transfers.json"

Mode = Literal["taxi", "private_minivan", "shuttle", "metro", "speedboat", "seaplane", "flight_speedboat"]
Issue = Literal[
    "unavailable_at_pickup",  # в это время не ходит, depart_at — ближайший рейс по расписанию
    "pets_forbidden",
    "pets_unknown",
    "child_seat_missing",
    "child_seat_unknown",
    "extra_vehicle",  # группа не помещается в одну машину
    "night_surcharge",
    "long_transfer",  # дольше 90 минут
    "prebook_required",  # бронировать заранее, за prebook_hours
    "luggage_limit",
]
CHILD_SEAT_MAX_AGE = 12  # детям младше нужно кресло в машине, старше платят как взрослые
INFANT_MAX_AGE = 2  # младше — бесплатно в транспорте с оплатой за человека
LONG_TRANSFER_MIN = 90


class UnsupportedTransfer(ValueError):
    """В базе нет трансфера из этого аэропорта в этот отель."""


class Hours(BaseModel):
    """Интервал местного времени. Если to меньше from, интервал переходит через полночь; to=00:00 — до полуночи."""

    model_config = ConfigDict(populate_by_name=True)

    start: time = Field(alias="from")
    end: time = Field(alias="to")

    def contains(self, t: time) -> bool:
        if self.start < self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end

    def next_start(self, moment: datetime) -> datetime:
        day = moment.date() if moment.time() < self.start else moment.date() + timedelta(days=1)
        return datetime.combine(day, self.start, tzinfo=moment.tzinfo)


class Pricing(BaseModel):
    type: Literal["per_vehicle", "per_person"]
    price_rub: int = Field(ge=0)  # за машину или за взрослого
    capacity: int | None = Field(default=None, ge=1)  # мест в машине, только для per_vehicle
    child_price_rub: int | None = Field(default=None, ge=0)  # только для per_person
    night_multiplier: float = Field(default=1.0, ge=1.0)
    night_hours: Hours | None = None


class TransferOption(BaseModel):
    id: str
    mode: Mode
    duration_min: int = Field(gt=0)
    distance_km: float = Field(ge=0)
    pricing: Pricing
    hours: Hours | None  # None — круглосуточно
    luggage_limit: str | None
    child_seat: bool | None  # None — неизвестно
    pets: bool | None  # None — неизвестно
    prebook_hours: int = Field(ge=0)
    source: Literal["osrm", "synthetic"]
    labels: list[str]


class HotelTransfers(BaseModel):
    hotel_id: str
    airport: str
    tz: str
    labels: list[str]
    options: list[TransferOption] = Field(min_length=1)


class Travelers(BaseModel):
    adults: int = Field(default=1, ge=1)
    children_ages: list[int] = Field(default_factory=list)
    pets: int = Field(default=0, ge=0)


class TransferQuote(BaseModel):
    option_id: str
    mode: Mode
    available: bool  # можно уехать в запрошенное время
    depart_at: datetime  # местное время отправления: запрошенное или ближайшее по расписанию
    arrive_at: datetime
    duration_min: int
    distance_km: float
    vehicles: int | None  # сколько машин нужно группе, только для per_vehicle
    price_rub: int  # за всю группу
    issues: list[Issue]
    labels: list[str]


class TransferBase:
    def __init__(self, records: list[HotelTransfers]) -> None:
        self._records = {(r.hotel_id, r.airport): r for r in records}

    @classmethod
    def load(cls, path: Path | None = None) -> "TransferBase":
        path = path or Path(os.environ.get("TRANSFERS_PATH", DEFAULT_PATH))
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls([HotelTransfers.model_validate(r) for r in data["transfers"]])

    def get(self, hotel_id: str, airport: str) -> HotelTransfers:
        try:
            return self._records[(hotel_id, airport)]
        except KeyError:
            raise UnsupportedTransfer(f"Нет трансфера {airport} → {hotel_id}") from None

    def __iter__(self) -> Iterator[HotelTransfers]:
        return iter(self._records.values())


@lru_cache
def default_base() -> TransferBase:
    return TransferBase.load()


def quote_transfers(
    hotel_id: str,
    airport: str,
    pickup_at: datetime,
    travelers: Travelers,
    base: TransferBase | None = None,
) -> list[TransferQuote]:
    """Все варианты трансфера для группы: сначала доступные без запретов, затем по цене."""
    if pickup_at.tzinfo is None:
        raise ValueError("pickup_at нужен с часовым поясом")
    record = (base or default_base()).get(hotel_id, airport)
    local = pickup_at.astimezone(ZoneInfo(record.tz))
    quotes = [_quote(option, local, travelers) for option in record.options]
    return sorted(quotes, key=lambda q: (not q.available, "pets_forbidden" in q.issues, q.price_rub))


def _quote(option: TransferOption, pickup: datetime, travelers: Travelers) -> TransferQuote:
    issues: list[Issue] = []
    depart = pickup
    available = option.hours is None or option.hours.contains(pickup.time())
    if option.hours is not None and not available:
        depart = option.hours.next_start(pickup)
        issues.append("unavailable_at_pickup")

    price, vehicles, night = _price(option.pricing, travelers, depart)
    if vehicles and vehicles > 1:
        issues.append("extra_vehicle")
    if night:
        issues.append("night_surcharge")
    if travelers.pets:
        if option.pets is False:
            issues.append("pets_forbidden")
        elif option.pets is None:
            issues.append("pets_unknown")
    if option.pricing.type == "per_vehicle" and any(age < CHILD_SEAT_MAX_AGE for age in travelers.children_ages):
        if option.child_seat is False:
            issues.append("child_seat_missing")
        elif option.child_seat is None:
            issues.append("child_seat_unknown")
    if option.duration_min > LONG_TRANSFER_MIN:
        issues.append("long_transfer")
    if option.prebook_hours >= 24:
        issues.append("prebook_required")
    if option.luggage_limit:
        issues.append("luggage_limit")

    return TransferQuote(
        option_id=option.id,
        mode=option.mode,
        available=available,
        depart_at=depart,
        arrive_at=depart + timedelta(minutes=option.duration_min),
        duration_min=option.duration_min,
        distance_km=option.distance_km,
        vehicles=vehicles,
        price_rub=price,
        issues=issues,
        labels=option.labels,
    )


def _price(pricing: Pricing, travelers: Travelers, depart: datetime) -> tuple[int, int | None, bool]:
    night = (
        pricing.night_multiplier > 1 and pricing.night_hours is not None and pricing.night_hours.contains(depart.time())
    )
    multiplier = pricing.night_multiplier if night else 1.0
    if pricing.type == "per_vehicle":
        assert pricing.capacity, "у per_vehicle должна быть вместимость"
        vehicles = math.ceil((travelers.adults + len(travelers.children_ages)) / pricing.capacity)
        return round(vehicles * pricing.price_rub * multiplier), vehicles, night
    child_price = pricing.child_price_rub if pricing.child_price_rub is not None else pricing.price_rub
    total = travelers.adults * pricing.price_rub
    for age in travelers.children_ages:
        if age >= CHILD_SEAT_MAX_AGE:
            total += pricing.price_rub
        elif age >= INFANT_MAX_AGE:
            total += child_price
    return round(total * multiplier), None, night
