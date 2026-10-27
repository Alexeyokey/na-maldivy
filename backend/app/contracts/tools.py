"""Входы и выходы travel-инструментов D1 v0.1.

Все цены — целые рубли за указанную группу. Перелёт/поезд — в одну сторону,
отель — за весь срок, трансфер — за всю поездку. Багаж оплачивается отдельно.
Даты рейсов и поездов — местное время соответствующей точки с UTC-смещением.
"""

from datetime import UTC, date, time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, AwareDatetime, BaseModel, Field, StringConstraints, field_validator, model_validator

from app.contracts.identifiers import canonical_hotel_id

IATACode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Rubles = Annotated[int, Field(strict=True, ge=0)]
ChildAge = Annotated[int, Field(strict=True, ge=0, le=17)]
KnownBoolean = Annotated[bool, Field(strict=True)]
TransferIssue = Literal[
    "unavailable_at_pickup",
    "pets_forbidden",
    "pets_unknown",
    "child_seat_missing",
    "child_seat_unknown",
    "extra_vehicle",
    "night_surcharge",
    "long_transfer",
    "prebook_required",
    "luggage_limit",
]


class Contract(BaseModel):
    model_config = {"extra": "forbid", "revalidate_instances": "always"}


class Place(Contract):
    kind: Literal["airport", "hotel", "city"]
    id: Annotated[str, Field(strict=True, min_length=1)] | None = None
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

    @model_validator(mode="after")
    def stable_hotel_identity(self) -> "Place":
        if self.kind == "hotel":
            if self.id is None:
                raise ValueError("Hotel place requires a stable hotel ID")
            self.id = canonical_hotel_id(self.id)
        elif self.id is not None and not self.id.strip():
            raise ValueError("Place ID cannot contain only whitespace")
        return self


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
    hotel_id: str
    offer_id: str = Field(min_length=1, validation_alias=AliasChoices("offer_id", "id"))
    name: str
    place: Place
    check_in: date
    check_out: date
    price_rub: Rubles = Field(description="Полная стоимость за все ночи и всю группу")
    stars: Annotated[int, Field(strict=True, ge=1, le=5)] | None = None
    rating: Annotated[float, Field(strict=True, ge=0, le=10, allow_inf_nan=False)] | None = None
    adults_only: KnownBoolean | None = None
    pets_allowed: KnownBoolean | None = None
    refundable: KnownBoolean | None = None
    check_in_time: time | None = None
    check_out_time: time | None = None
    reception_24h: KnownBoolean | None = None
    reception_closes_at: time | None = None
    early_check_in_available: KnownBoolean | None = None
    early_check_in_price_rub: Rubles | None = None
    is_synthetic: bool

    @field_validator("hotel_id")
    @classmethod
    def canonical_identity(cls, value: str) -> str:
        return canonical_hotel_id(value)

    @field_validator("check_in_time", "check_out_time", "reception_closes_at")
    @classmethod
    def local_clock(cls, value: time | None) -> time | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("Hotel clock times must be local times without an offset")
        return value

    @property
    def id(self) -> str:
        """Legacy read-only Python access; serialized output uses offer_id."""
        return self.offer_id

    @model_validator(mode="after")
    def positive_stay(self) -> "HotelOffer":
        if self.check_out <= self.check_in:
            raise ValueError("Дата выезда должна быть позже даты заезда")
        if self.place.kind != "hotel" or self.place.id != self.hotel_id:
            raise ValueError("Hotel offer place must be kind=hotel and its ID must equal hotel_id")
        if self.reception_closes_at is not None and self.reception_24h is not False:
            raise ValueError("Known reception closing time requires reception_24h=False")
        if self.early_check_in_price_rub is not None and self.early_check_in_available is not True:
            raise ValueError("Known early check-in price requires early_check_in_available=True")
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


class TransferRequest(Contract):
    """Public airport-to-hotel request; physical places come from source data.

    Any explicit UTC offset is accepted. The resolving adapter constructs a
    TransferQuery, which converts that instant into the airport's local zone.
    """

    airport: IATACode
    hotel_id: str
    pickup_at: AwareDatetime
    travelers: Travelers = Field(default_factory=Travelers)

    @field_validator("hotel_id")
    @classmethod
    def canonical_identity(cls, value: str) -> str:
        return canonical_hotel_id(value)


class TransferQuery(Contract):
    """Resolved transfer query; pickup is normalized to the origin's timezone."""

    origin: Place
    destination: Place
    pickup_at: AwareDatetime
    travelers: Travelers = Field(default_factory=Travelers)

    @model_validator(mode="after")
    def local_pickup(self) -> "TransferQuery":
        try:
            self.pickup_at = self.pickup_at.astimezone(ZoneInfo(self.origin.timezone))
        except OverflowError as exc:
            raise ValueError("Local pickup is outside the supported calendar") from exc
        return self


class TransferHours(Contract):
    """Origin-local daily opening window; end exclusive, null means known 24h."""

    model_config = {"validate_by_name": True, "validate_by_alias": True, "serialize_by_alias": True}

    from_: time = Field(alias="from")
    to: time

    @field_validator("from_", "to")
    @classmethod
    def local_clock(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("Transfer hours must be origin-local clock times without offsets")
        return value

    @model_validator(mode="after")
    def distinct_bounds(self) -> "TransferHours":
        if self.from_ == self.to:
            raise ValueError("Equal transfer hours are ambiguous; use null for known 24-hour service")
        return self

    def contains(self, value: time) -> bool:
        if value.tzinfo is not None:
            raise ValueError("Expected a local clock time without an offset")
        if self.from_ < self.to:
            return self.from_ <= value < self.to
        return value >= self.from_ or value < self.to


class Transfer(Contract):
    id: str = Field(min_length=1)
    origin: Place
    destination: Place
    pickup_at: AwareDatetime
    hours: TransferHours | None
    available: KnownBoolean
    depart_at: AwareDatetime | None
    vehicles: Annotated[int, Field(strict=True, ge=1)] | None = None
    duration_minutes: int = Field(gt=0)
    price_rub: Rubles = Field(description="Цена за всю группу, без умножения на число пассажиров")
    mode: Literal["taxi", "minivan", "shuttle", "bus", "metro", "boat", "seaplane", "flight_speedboat"] = "taxi"
    pets: KnownBoolean | None = None
    child_seat: KnownBoolean | None = None
    luggage_limit: str | None = None
    prebook_hours: Annotated[int, Field(strict=True, ge=0)] | None = None
    distance_km: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)] | None = None
    issues: list[TransferIssue] = Field(default_factory=list)
    is_synthetic: bool

    @model_validator(mode="after")
    def local_pickup(self) -> "Transfer":
        if self.pickup_at.utcoffset() != self.pickup_at.astimezone(ZoneInfo(self.origin.timezone)).utcoffset():
            raise ValueError("Время подачи должно быть местным для точки отправления")
        if self.available and self.depart_at is None:
            raise ValueError("Available transfer must depart at the requested pickup instant")
        if self.depart_at is None:
            return self
        origin_zone = ZoneInfo(self.origin.timezone)
        if self.depart_at.utcoffset() != self.depart_at.astimezone(origin_zone).utcoffset():
            raise ValueError("Transfer departure must use the origin's local UTC offset")
        departure = self.depart_at.astimezone(UTC)
        pickup = self.pickup_at.astimezone(UTC)
        if self.available and departure != pickup:
            raise ValueError("Available transfer must depart at the requested pickup instant")
        if not self.available and departure <= pickup:
            raise ValueError("Unavailable transfer departure must be strictly after the requested pickup")
        if self.hours is not None and not self.hours.contains(self.depart_at.astimezone(origin_zone).time()):
            raise ValueError("Transfer departure is outside operating hours")
        return self


class DestinationQuery(Contract):
    month: int = Field(ge=1, le=12)
    origin: IATACode
    travelers: Travelers = Field(default_factory=Travelers)
    nights: int = Field(default=7, ge=1)
    budget_rub: Rubles | None = None
    min_water_temperature_c: float | None = None
    visa_free_only: bool = False


class Destination(Contract):
    place: Place
    country: str
    month: int = Field(ge=1, le=12)
    air_temperature_c: float | None
    water_temperature_c: float | None
    flight_duration_minutes: int | None = Field(gt=0)
    visa_required: KnownBoolean | None
    estimated_total_price_rub: Rubles | None
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
