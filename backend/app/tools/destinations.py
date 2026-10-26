"""Versioned destination catalogue and deterministic, source-aware suggestions.

This module deliberately does not depend on D1's unapproved agent DTOs. The IATA
code in a record is an anchor airport for a destination, not every airport in the
city. Climate values are monthly climatology, never a weather forecast. Flight
durations are estimates, not a promise that a direct flight exists.
"""

import os
import unicodedata
from collections.abc import Mapping
from datetime import date, timedelta
from functools import lru_cache
from math import ceil
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_serializer, field_validator, model_validator

Month = Annotated[int, Field(strict=True, ge=1, le=12)]
Rubles = Annotated[int, Field(strict=True, ge=0)]
Tag = Literal["beach", "mountains", "sightseeing", "family"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Source(FrozenModel):
    title: Annotated[str, Field(min_length=1)]
    url: HttpUrl
    license: str | None
    retrieved_at: date
    period: str | None
    method: Annotated[str, Field(min_length=1)]


class Location(FrozenModel):
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    timezone: str

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class MonthlyClimate(FrozenModel):
    month: Month
    air_temp_c: Annotated[float, Field(ge=-90, le=65)] | None
    water_temp_c: Annotated[float, Field(ge=-5, le=45)] | None
    precipitation_mm: Annotated[float, Field(ge=0, le=5000)] | None


class Climate(FrozenModel):
    air_source: str | None
    water_source: str | None
    months: tuple[MonthlyClimate, ...]
    notes: str

    @model_validator(mode="after")
    def complete_year_with_provenance(self) -> "Climate":
        if tuple(item.month for item in self.months) != tuple(range(1, 13)):
            raise ValueError("Climate must contain exactly months 1..12 in order")
        if self.air_source is None and any(
            item.air_temp_c is not None or item.precipitation_mm is not None for item in self.months
        ):
            raise ValueError("Known air temperature or precipitation requires air_source")
        if self.water_source is None and any(item.water_temp_c is not None for item in self.months):
            raise ValueError("Known water temperature requires water_source")
        return self


class Visa(FrozenModel):
    citizenship: Literal["RU"] = "RU"
    passport_type: Literal["ordinary"] = "ordinary"
    purpose: Literal["tourism"] = "tourism"
    regime: Literal["domestic", "exempt", "on_arrival", "evisa", "required", "unknown"]
    verification_status: Literal["official_verified", "partial", "unknown", "domestic"] = "unknown"
    effective_from: date | None = None
    effective_until: date | None = None
    max_stay_days: Annotated[int, Field(strict=True, gt=0)] | None
    rolling_window_days: Annotated[int, Field(strict=True, gt=0)] | None
    checked_on: date | None
    source: str | None
    conditions: str

    @model_validator(mode="after")
    def evidenced_regime(self) -> "Visa":
        if self.regime != "unknown" and (self.source is None or self.checked_on is None):
            raise ValueError("Known visa regime requires source and checked_on")
        if self.regime == "unknown" and (self.max_stay_days is not None or self.rolling_window_days is not None):
            raise ValueError("Unknown visa regime cannot promise a stay length")
        if self.regime == "domestic" and self.verification_status != "domestic":
            raise ValueError("Domestic regime requires domestic verification_status")
        if self.verification_status == "official_verified" and self.regime == "unknown":
            raise ValueError("Unknown visa regime cannot be officially verified")
        if self.verification_status == "domestic" and self.regime != "domestic":
            raise ValueError("Domestic verification_status requires domestic regime")
        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_from > self.effective_until
        ):
            raise ValueError("Visa effective_from is later than effective_until")
        if (
            self.max_stay_days is not None
            and self.rolling_window_days is not None
            and self.max_stay_days > self.rolling_window_days
        ):
            raise ValueError("Maximum stay exceeds rolling window")
        return self


class FlightPrices(FrozenModel):
    MOW: Rubles | None
    LED: Rubles | None


class Prices(FrozenModel):
    round_trip_flight_rub: FlightPrices
    hotel_night_rub: Rubles | None
    status: Literal["pending", "verified", "synthetic"]
    source: str | None
    notes: str

    @model_validator(mode="after")
    def evidenced_prices(self) -> "Prices":
        any_known = any(
            value is not None
            for value in (self.round_trip_flight_rub.MOW, self.round_trip_flight_rub.LED, self.hotel_night_rub)
        )
        if self.status == "pending" and any_known:
            raise ValueError("Pending prices must be null")
        if self.status != "pending" and (not any_known or self.source is None):
            raise ValueError("Known or synthetic prices require values and a source")
        return self


class FlightTimeEstimate(FrozenModel):
    minutes: Annotated[int, Field(strict=True, gt=0, le=2880)]
    method: Annotated[str, Field(min_length=1)]


class FlightTimeEstimates(FrozenModel):
    MOW: FlightTimeEstimate | None = None
    LED: FlightTimeEstimate | None = None


class DestinationRecord(FrozenModel):
    id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    name: Annotated[str, Field(min_length=1)]
    country: Annotated[str, Field(min_length=1)]
    country_code: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    iata: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    aliases: tuple[str, ...]
    location: Location
    geo_source: str
    tags: tuple[Tag, ...]
    climate: Climate
    visa: Visa
    prices: Prices
    flight_time_estimates: FlightTimeEstimates

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, tags: tuple[Tag, ...]) -> tuple[Tag, ...]:
        if len(set(tags)) != len(tags):
            raise ValueError("Duplicate tags")
        return tags


class Catalogue(FrozenModel):
    schema_version: Literal[1]
    as_of: date
    sources: Mapping[str, Source]
    destinations: tuple[DestinationRecord, ...]

    @field_serializer("sources")
    def serialize_sources(self, value: Mapping[str, Source]) -> dict[str, Source]:
        return dict(value)

    @model_validator(mode="after")
    def consistent_catalogue(self) -> "Catalogue":
        if not self.destinations:
            raise ValueError("Catalogue is empty")
        for field in ("id", "iata"):
            values = [getattr(item, field) for item in self.destinations]
            if len(set(values)) != len(values):
                raise ValueError(f"Duplicate destination {field}")
        for source in self.sources.values():
            if source.retrieved_at > self.as_of:
                raise ValueError("Source retrieved_at is later than catalogue as_of")
        for item in self.destinations:
            refs = [
                item.geo_source,
                item.climate.air_source,
                item.climate.water_source,
                item.visa.source,
                item.prices.source,
            ]
            missing = [reference for reference in refs if reference is not None and reference not in self.sources]
            if missing:
                raise ValueError(f"Unknown source references for {item.id}: {missing}")
            if item.visa.checked_on is not None and item.visa.checked_on > self.as_of:
                raise ValueError(f"Visa checked_on is later than catalogue as_of: {item.id}")
        # frozen=True alone does not prevent dict item assignment. Protect the shared cache too.
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))
        return self


def _data_path(path: str | Path | None) -> Path:
    selected = path if path is not None else os.environ.get("DESTINATIONS_DATA_PATH")
    if selected is None:
        selected = Path(__file__).resolve().parents[3] / "data" / "destinations.yaml"
    return Path(selected).expanduser().resolve()


@lru_cache(maxsize=4)
def _load_catalogue(path: Path) -> Catalogue:
    with path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    return Catalogue.model_validate(raw)


def load_catalogue(path: str | Path | None = None) -> Catalogue:
    """Load and validate once per path/process. Restart after editing a catalogue.

    DESTINATIONS_DATA_PATH supports deployments where data is outside the repo.
    Models, nested tuples, and the source mapping are immutable, so callers cannot
    mutate a cached result and affect later requests.
    """
    return _load_catalogue(_data_path(path))


def clear_catalogue_cache() -> None:
    """Explicit cache invalidation for data reloads and tests."""
    _load_catalogue.cache_clear()


def _catalogue(catalogue: Catalogue | Mapping | None, data_path: str | Path | None) -> Catalogue:
    if catalogue is not None and data_path is not None:
        raise ValueError("Pass either catalogue or data_path, not both")
    if catalogue is None:
        return load_catalogue(data_path)
    return catalogue if isinstance(catalogue, Catalogue) else Catalogue.model_validate(catalogue)


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().replace("ё", "е").split())


class UnknownDestinationError(ValueError):
    """An alias is not present in this finite catalogue."""


def resolve_iata(
    city_alias: str,
    *,
    catalogue: Catalogue | Mapping | None = None,
    data_path: str | Path | None = None,
) -> tuple[str, ...]:
    """Return all anchor IATA matches; never silently pick an ambiguous alias.

    A non-empty tuple can contain multiple airports. The caller must ask which
    destination was meant rather than treating the first one as authoritative.
    """
    if not isinstance(city_alias, str) or not city_alias.strip():
        raise ValueError("city_alias must be a non-empty string")
    needle = _normalize(city_alias)
    matches = sorted(
        {
            record.iata
            for record in _catalogue(catalogue, data_path).destinations
            if needle in {_normalize(name) for name in (record.id, record.name, record.iata, *record.aliases)}
        }
    )
    if not matches:
        raise UnknownDestinationError(f"Destination is not in the catalogue: {city_alias!r}")
    return tuple(matches)


class Preferences(FrozenModel):
    tags: tuple[Tag, ...] = ()
    min_water_temp_c: Annotated[float, Field(ge=-5, le=45)] | None = None
    max_flight_minutes: Annotated[int, Field(strict=True, gt=0)] | None = None
    visa_free_only: bool = False
    max_precipitation_mm: Annotated[float, Field(ge=0)] | None = None

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, tags: tuple[Tag, ...]) -> tuple[Tag, ...]:
        return tuple(dict.fromkeys(tags))


class SuggestionQuery(FrozenModel):
    month: Month
    preferences: Preferences = Preferences()
    origin: Literal["MOW", "LED"]
    budget: Rubles | None = None
    nights: Annotated[int, Field(strict=True, ge=1, le=90)] = 7
    travelers: Annotated[int, Field(strict=True, ge=1, le=20)] = 1
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 10
    departure_date: date | None = None

    @model_validator(mode="after")
    def valid_travel_dates(self) -> "SuggestionQuery":
        if self.departure_date is not None:
            if self.departure_date.month != self.month:
                raise ValueError("departure_date must be in the selected month")
            try:
                self.departure_date + timedelta(days=self.nights)
            except OverflowError as exc:
                raise ValueError("Trip ends outside supported calendar") from exc
        return self


class Exclusion(FrozenModel):
    destination_id: str
    reasons: tuple[str, ...]


class ClimateRisk(FrozenModel):
    code: Literal["wet_month", "hot_month", "freezing_month"]
    message: str


class DestinationSuggestion(FrozenModel):
    destination: DestinationRecord
    climate: MonthlyClimate
    flight_minutes_estimate: int | None
    trip_cost_estimate_rub: int | None
    score: int
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    risks: tuple[ClimateRisk, ...]


class SuggestionResponse(FrozenModel):
    query: SuggestionQuery
    catalogue_as_of: date
    candidates: tuple[DestinationSuggestion, ...]
    excluded: tuple[Exclusion, ...]
    matched_count: int
    warnings: tuple[str, ...]
    sources: dict[str, Source]


def _origin(value: str) -> Literal["MOW", "LED"]:
    if not isinstance(value, str):
        raise ValueError("origin must be MOW (or SVO/DME/VKO) or LED")
    normalized = value.strip().upper()
    if normalized in {"MOW", "SVO", "DME", "VKO"}:
        return "MOW"
    if normalized == "LED":
        return "LED"
    raise ValueError("Supported origin estimates: MOW (SVO/DME/VKO) and LED")


def _climate_risks(climate: MonthlyClimate) -> tuple[ClimateRisk, ...]:
    """Simple disclosed thresholds, based on monthly normals rather than forecast."""
    risks = []
    if climate.precipitation_mm is not None and climate.precipitation_mm >= 150:
        risks.append(ClimateRisk(code="wet_month", message="Месячная климатическая норма осадков ≥150 мм."))
    if climate.air_temp_c is not None and climate.air_temp_c >= 35:
        risks.append(ClimateRisk(code="hot_month", message="Средняя температура воздуха за месяц ≥35 °C."))
    if climate.air_temp_c is not None and climate.air_temp_c <= 0:
        risks.append(ClimateRisk(code="freezing_month", message="Средняя температура воздуха за месяц ≤0 °C."))
    return tuple(risks)


def suggest_destinations(
    month: int,
    preferences: Preferences | Mapping | None = None,
    origin: str = "MOW",
    budget: int | None = None,
    *,
    nights: int = 7,
    travelers: int = 1,
    limit: int = 10,
    departure_date: date | None = None,
    catalogue: Catalogue | Mapping | None = None,
    data_path: str | Path | None = None,
) -> SuggestionResponse:
    """Filter and rank catalogue facts, returning explanations and rejected IDs.

    Tags are all required; each matching requested tag adds 10 points. Additional
    disclosed comfort points: mean air 20..30 °C (+2), water >=24 °C (+2), monthly
    precipitation <=80 mm (+1). Equal scores sort by stable destination ID.

    Budget is total group RUB. Future populated prices mean return-flight fare
    per traveler + hotel quote per room/night (two travelers per room). This is a
    planning estimate, excluding baggage, transfers, meals, visa and extras; it
    must not substitute for R2's complete trip price. Pending/synthetic prices
    cannot satisfy an explicit budget. Unknown facts cannot pass a hard filter.
    Visa exemption means domestic/exempt only (not visa on arrival/e-visa), for
    ordinary Russian passports; a foreign exemption with an unknown numeric
    stay limit cannot pass the strict filter. Source conditions still apply.
    """
    query = SuggestionQuery(
        month=month,
        preferences=Preferences.model_validate(preferences) if preferences is not None else Preferences(),
        origin=_origin(origin),
        budget=budget,
        nights=nights,
        travelers=travelers,
        limit=limit,
        departure_date=departure_date,
    )
    data = _catalogue(catalogue, data_path)
    results: list[DestinationSuggestion] = []
    excluded: list[Exclusion] = []
    prefs = query.preferences
    for record in data.destinations:
        climate = record.climate.months[query.month - 1]
        flight = getattr(record.flight_time_estimates, query.origin)
        flight_price = getattr(record.prices.round_trip_flight_rub, query.origin)
        hotel_price = record.prices.hotel_night_rub
        trip_cost = (
            flight_price * query.travelers + hotel_price * query.nights * ceil(query.travelers / 2)
            if record.prices.status == "verified" and flight_price is not None and hotel_price is not None
            else None
        )
        rejected: list[str] = []
        visa_start = query.departure_date or data.as_of
        visa_end = visa_start + timedelta(days=query.nights) if query.departure_date else visa_start
        visa_period_valid = not (
            (record.visa.effective_until is not None and record.visa.effective_until < visa_end)
            or (record.visa.effective_from is not None and record.visa.effective_from > visa_start)
        )
        if record.iata == query.origin:
            rejected.append("same_as_origin")
        if not set(prefs.tags).issubset(record.tags):
            rejected.append("missing_required_tags")
        if prefs.min_water_temp_c is not None:
            if climate.water_temp_c is None:
                rejected.append("unknown_water_temperature")
            elif climate.water_temp_c < prefs.min_water_temp_c:
                rejected.append("water_too_cold")
        if prefs.max_precipitation_mm is not None:
            if climate.precipitation_mm is None:
                rejected.append("unknown_precipitation")
            elif climate.precipitation_mm > prefs.max_precipitation_mm:
                rejected.append("too_much_precipitation")
        if prefs.max_flight_minutes is not None:
            if flight is None:
                rejected.append("unknown_flight_duration")
            elif flight.minutes > prefs.max_flight_minutes:
                rejected.append("flight_too_long")
        if prefs.visa_free_only:
            if record.visa.regime == "unknown":
                rejected.append("unknown_visa_rules")
            elif record.visa.regime not in {"domestic", "exempt"}:
                rejected.append("visa_required")
            elif record.visa.regime == "exempt" and record.visa.verification_status != "official_verified":
                rejected.append("unverified_visa_rules")
            if record.visa.regime == "exempt" and record.visa.max_stay_days is None:
                rejected.append("unknown_visa_stay_limit")
            if not visa_period_valid:
                rejected.append(
                    "visa_rules_not_effective_for_trip"
                    if query.departure_date
                    else "visa_rules_not_effective_at_snapshot"
                )
            if record.visa.max_stay_days is not None and query.nights + 1 > record.visa.max_stay_days:
                rejected.append("stay_exceeds_visa_limit")
        if query.budget is not None:
            if trip_cost is None:
                rejected.append("unknown_price")
            elif trip_cost > query.budget:
                rejected.append("over_budget")
        if rejected:
            excluded.append(Exclusion(destination_id=record.id, reasons=tuple(rejected)))
            continue
        score = 10 * len(prefs.tags)
        reasons = [f"Совпадает предпочтение {tag}: +10." for tag in prefs.tags]
        if climate.air_temp_c is not None and 20 <= climate.air_temp_c <= 30:
            score += 2
            reasons.append("Средняя температура воздуха 20–30 °C: +2.")
        if climate.water_temp_c is not None and climate.water_temp_c >= 24:
            score += 2
            reasons.append("Средняя температура воды от 24 °C: +2.")
        if climate.precipitation_mm is not None and climate.precipitation_mm <= 80:
            score += 1
            reasons.append("Месячная норма осадков до 80 мм: +1.")
        warnings = [
            "Время перелёта — географическая оценка; наличие маршрута и пересадок не проверено."
            if flight is not None
            else "Время перелёта неизвестно."
        ]
        if query.departure_date is None:
            warnings.append("Даты поездки не заданы: срок действия визового правила проверен только на дату среза.")
        elif not visa_period_valid:
            warnings.append(
                "Даты поездки выходят за известный срок действия визового правила; применимость не подтверждена."
            )
        else:
            warnings.append(
                "Даты поездки не выходят за известные границы действия визового правила. "
                "Неуказанные границы и изменения после среза неизвестны."
            )
        if record.visa.max_stay_days is not None and query.nights + 1 > record.visa.max_stay_days:
            warnings.append("Длина поездки превышает сохранённый лимит пребывания; требуется другое основание въезда.")
        if record.visa.regime != "domestic" and record.visa.max_stay_days is None:
            warnings.append(
                "Численный лимит пребывания не установлен; допустимая длительность поездки не подтверждена."
            )
        if record.visa.regime == "unknown":
            warnings.append("Визовый режим не проверен; неизвестное не означает безвизовый въезд.")
        else:
            warnings.append(
                "Визовый режим — сохранённая справка для граждан РФ; перед поездкой сверить условия источника."
            )
        if trip_cost is None:
            warnings.append("Цена поездки неизвестна; подбор по бюджету пока недоступен для направления.")
        else:
            warnings.append("Стоимость — ориентир перелёт + отель; багаж, трансферы и прочие доплаты не включены.")
        if any(value is None for value in (climate.air_temp_c, climate.water_temp_c, climate.precipitation_mm)):
            warnings.append("Часть климатических данных отсутствует; неизвестные значения не заменены нулями.")
        results.append(
            DestinationSuggestion(
                destination=record,
                climate=climate,
                flight_minutes_estimate=flight.minutes if flight is not None else None,
                trip_cost_estimate_rub=trip_cost,
                score=score,
                reasons=tuple(reasons),
                warnings=tuple(warnings),
                risks=_climate_risks(climate),
            )
        )
    results.sort(key=lambda item: (-item.score, item.destination.id))
    return SuggestionResponse(
        query=query,
        catalogue_as_of=data.as_of,
        candidates=tuple(results[: query.limit]),
        excluded=tuple(sorted(excluded, key=lambda item: item.destination_id)),
        matched_count=len(results),
        warnings=(
            "Климатические значения — многолетние месячные средние, не прогноз на даты поездки.",
            "Справочник конечен; отсутствие направления не означает отсутствие возможной поездки.",
        ),
        sources=dict(data.sources),
    )
