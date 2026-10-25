"""Входы и выходы travel-инструментов, версия 0 для O1.

Все цены — целые рубли за указанную группу. Перелёт/поезд — в одну сторону,
отель — за весь срок, трансфер — за всю поездку. Багаж оплачивается отдельно.
Даты рейсов и поездов — местное время соответствующей точки с UTC-смещением.
"""

from datetime import UTC, date, time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints, field_validator, model_validator

IATACode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Rubles = Annotated[int, Field(strict=True, ge=0)]
ChildAge = Annotated[int, Field(strict=True, ge=0, le=17)]


class Contract(BaseModel):
    model_config = {"extra": "forbid", "revalidate_instances": "always"}


class Place(Contract):
    iata: IATACode
    name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str = Field(description="Часовой пояс IANA, например Europe/Moscow")

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Неизвестный часовой пояс IANA") from exc
        return value


class Travelers(Contract):
    adults: int = Field(default=1, strict=True, ge=1)
    children_ages: list[ChildAge] = Field(default_factory=list)
    pets: int = Field(default=0, strict=True, ge=0)

    @property
    def count(self) -> int:
        return self.adults + len(self.children_ages)


class FlightSearch(Contract):
    origin: IATACode
    destination: IATACode
    departure_date: date
    travelers: Travelers = Field(default_factory=Travelers)
    direct_only: bool = False
    max_price_rub: Rubles | None = None


class Journey(Contract):
    id: str = Field(min_length=1)
    origin: Place
    destination: Place
    departure_at: AwareDatetime
    arrival_at: AwareDatetime
    price_rub: Rubles = Field(description="Цена за всю группу в одну сторону")
    is_synthetic: bool

    @model_validator(mode="after")
    def valid_times(self) -> "Journey":
        if self.arrival_at.astimezone(UTC) <= self.departure_at.astimezone(UTC):
            raise ValueError("Прилёт/прибытие должно быть позже отправления")
        for value, place in ((self.departure_at, self.origin), (self.arrival_at, self.destination)):
            if value.utcoffset() != value.astimezone(ZoneInfo(place.timezone)).utcoffset():
                raise ValueError("UTC-смещение времени не совпадает с часовым поясом точки")
        return self


class Flight(Journey):
    carrier: str
    stops: int = Field(default=0, ge=0)
    baggage_price_rub: Rubles = 0
    is_overnight: bool = False


class HotelSearch(Contract):
    destination: IATACode
    check_in: date
    check_out: date
    travelers: Travelers = Field(default_factory=Travelers)
    min_stars: int = Field(default=0, ge=0, le=5)
    refundable_only: bool = False
    max_price_rub: Rubles | None = None

    @model_validator(mode="after")
    def positive_stay(self) -> "HotelSearch":
        if self.check_out <= self.check_in:
            raise ValueError("Дата выезда должна быть позже даты заезда")
        return self


class HotelOffer(Contract):
    id: str = Field(min_length=1)
    name: str
    place: Place
    check_in: date
    check_out: date
    price_rub: Rubles = Field(description="Полная стоимость за все ночи и всю группу")
    stars: int = Field(ge=0, le=5)
    rating: float = Field(ge=0, le=10)
    adults_only: bool = False
    pets_allowed: bool = False
    refundable: bool = True
    check_in_time: time = time(14)
    check_out_time: time = time(12)
    reception_closes_at: time | None = None  # None — круглосуточно
    early_check_in_price_rub: Rubles | None = None
    is_synthetic: bool

    @model_validator(mode="after")
    def positive_stay(self) -> "HotelOffer":
        if self.check_out <= self.check_in:
            raise ValueError("Дата выезда должна быть позже даты заезда")
        return self


class TrainSearch(Contract):
    origin: IATACode
    destination: IATACode
    departure_date: date
    travelers: Travelers = Field(default_factory=Travelers)
    seat_class: Literal["platzkart", "coupe", "sv", "seated"] = "coupe"


class Train(Journey):
    number: str
    seat_class: Literal["platzkart", "coupe", "sv", "seated"]


class TransferQuery(Contract):
    origin: Place
    destination: Place
    pickup_at: AwareDatetime
    travelers: Travelers = Field(default_factory=Travelers)

    @model_validator(mode="after")
    def local_pickup(self) -> "TransferQuery":
        if self.pickup_at.utcoffset() != self.pickup_at.astimezone(ZoneInfo(self.origin.timezone)).utcoffset():
            raise ValueError("Время подачи должно быть местным для точки отправления")
        return self


class Transfer(Contract):
    id: str = Field(min_length=1)
    origin: Place
    destination: Place
    pickup_at: AwareDatetime
    duration_minutes: int = Field(gt=0)
    price_rub: Rubles = Field(description="Цена за всю группу, без умножения на число пассажиров")
    mode: Literal["taxi", "shuttle", "bus"] = "taxi"
    is_synthetic: bool

    @model_validator(mode="after")
    def local_pickup(self) -> "Transfer":
        if self.pickup_at.utcoffset() != self.pickup_at.astimezone(ZoneInfo(self.origin.timezone)).utcoffset():
            raise ValueError("Время подачи должно быть местным для точки отправления")
        return self


class DestinationQuery(Contract):
    month: int = Field(ge=1, le=12)
    origin: IATACode = "SVO"
    travelers: Travelers = Field(default_factory=Travelers)
    nights: int = Field(default=7, ge=1)
    budget_rub: Rubles | None = None
    min_water_temperature_c: float | None = None
    visa_free_only: bool = False


class Destination(Contract):
    place: Place
    country: str
    month: int = Field(ge=1, le=12)
    air_temperature_c: float
    water_temperature_c: float
    flight_duration_minutes: int = Field(gt=0)
    visa_required: bool
    estimated_total_price_rub: Rubles
    tags: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    is_synthetic: bool


class PriceCalendarQuery(Contract):
    origin: IATACode
    destination: IATACode
    month: Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
    travelers: Travelers = Field(default_factory=Travelers)

    @field_validator("month")
    @classmethod
    def valid_month(cls, value: str) -> str:
        date.fromisoformat(value + "-01")
        return value


class DayPrice(Contract):
    date: date
    price_rub: Rubles = Field(description="Минимальная цена перелёта за всю группу в одну сторону")
    is_synthetic: bool
