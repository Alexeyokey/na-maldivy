"""D2: evidenced route network, seeded demo flights, explicit Aviasales quotes.

Aviasales Data API is a historical search-price cache, not availability or a
booking API. Even a provider-backed Flight remains synthetic: group, luggage
and pet prices are modelled. Use search_with_provenance to inspect quote fields.
"""

import json
from dataclasses import dataclass
from datetime import datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import httpx
from pydantic import AwareDatetime, BaseModel, Field, TypeAdapter, ValidationError

from app.contracts.tools import Flight, FlightPriceSource, FlightSearch, Place, Travelers
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

ROUTES_PATH = DATA_DIR / "routes" / "network.json"
PRICING_VERSION = "infant-v2"
AVIASALES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def seated_count(travelers: Travelers) -> int:
    return travelers.adults + sum(age >= 2 for age in travelers.children_ages)


def group_fare(seat_price: int, travelers: Travelers) -> int:
    """Synthetic rule: under-two lap infants pay 10%, rounded half up once."""
    infants = sum(age < 2 for age in travelers.children_ages)
    return (seat_price * (10 * seated_count(travelers) + infants) + 5) // 10 + 1500 * travelers.pets


class FlightHttpClient(Protocol):
    """Injected HTTP boundary; D4 CachedHttpClient implements this interface.

    D2 owns neither the client lifecycle nor recording. Keeping the dependency
    structural lets flight search work before the independent D4 PR is merged.
    """

    @property
    def mode(self) -> Literal["record", "replay", "live"]: ...

    async def get(self, url: str, **kwargs: Any) -> httpx.Response: ...


@lru_cache(maxsize=8)
def _network(path: Path = ROUTES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _airport(code: str, path: Path = ROUTES_PATH) -> Place:
    try:
        return Place.model_validate(_network(path)["airports"][code])
    except KeyError as exc:
        raise ProviderResponseError(f"Unsupported airport in transport snapshot: {code}") from exc


def _route(origin: str, destination: str, path: Path = ROUTES_PATH) -> dict[str, Any] | None:
    origin = "SVO" if origin == "MOW" else origin
    destination = "SVO" if destination == "MOW" else destination
    for route in _network(path)["routes"]:
        if route["origin"] == origin and route["destination"] == destination:
            return dict(route)
        if route["origin"] == destination and route["destination"] == origin:
            return {**route, "origin": origin, "destination": destination, "via": list(reversed(route["via"]))}
    return None


def generate_flights(query: FlightSearch, *, seed: int = 42, routes_path: Path | None = None) -> list[Flight]:
    """Pure reproducible demo shared with D8: same day/group/seed => same fares."""
    routes_path = routes_path or resolve_data_dir() / "routes/network.json"
    route = _route(query.origin, query.destination, routes_path)
    if route is None or (query.direct_only and route["stops"]):
        return []
    origin, destination = _airport(route["origin"], routes_path), _airport(route["destination"], routes_path)
    season = 1200 if query.departure_date.month in (7, 8, 12) else 1000
    flights = []
    for variant, departure_time in enumerate((time(9), time(23, 30))):
        entropy = stable_number(seed, origin.iata, destination.iata, query.departure_date.isoformat(), variant)
        duration = route["synthetic_duration_minutes"] + entropy % 16
        departure = datetime.combine(query.departure_date, departure_time, ZoneInfo(origin.timezone))
        try:
            arrival = arrival_after(departure, duration, destination)
        except OverflowError:
            continue
        per_person = route["synthetic_base_price_rub"] * season * (900 + entropy % 201) // 1_000_000
        price = group_fare(per_person + variant * 1000, query.travelers)
        if query.max_price_rub is not None and price > query.max_price_rub:
            continue
        flights.append(
            Flight(
                id=f"synthetic-flight-{origin.iata}-{destination.iata}-{query.departure_date}-{variant}"
                f"-{party_id(query.travelers)}-s{seed}-{PRICING_VERSION}",
                origin=origin.model_copy(deep=True),
                destination=destination.model_copy(deep=True),
                departure_at=departure,
                arrival_at=arrival,
                price_rub=price,
                carrier="Demo Air (synthetic timetable and fare)",
                stops=route["stops"],
                baggage_price_rub=2000 * seated_count(query.travelers),
                is_overnight=arrival.date() > departure.date(),
                is_synthetic=True,
            )
        )
    return sorted(flights, key=lambda item: (item.price_rub, item.departure_at))


class FlightQuote(BaseModel):
    """One-seat cached provider observation; never a confirmed group fare."""

    origin_airport: str
    destination_airport: str
    departure_at: AwareDatetime
    price: int = Field(strict=True, ge=0)
    airline: str
    flight_number: str | int
    transfers: int = Field(strict=True, ge=0)
    duration_to: int | None = Field(default=None, strict=True, gt=0)
    link: str = ""
    currency: Literal["RUB"] = "RUB"
    # HTTP transport metadata does not change the provider's quoted fare.
    cache_mode: Literal["record", "replay", "live"] = Field(default="live", exclude=True)
    cache_hit: bool = Field(default=False, exclude=True)
    recorded_at: AwareDatetime | None = Field(default=None, exclude=True)


class AviasalesDataClient:
    def __init__(
        self, http: FlightHttpClient, token: str | None = None, *, max_pages: int = 5, routes_path: Path | None = None
    ):
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        self.http, self.token, self.max_pages = http, token, max_pages
        self.routes_path = routes_path or resolve_data_dir() / "routes/network.json"

    async def prices_for_dates(self, query: FlightSearch) -> list[FlightQuote]:
        if not self.token and self.http.mode != "replay":
            raise ProviderNotConfigured("Travelpayouts token is required in live/record mode")
        origin = "SVO" if query.origin == "MOW" else query.origin
        destination = "SVO" if query.destination == "MOW" else query.destination
        quotes = []
        for page in range(1, self.max_pages + 1):
            response = await self.http.get(
                AVIASALES_URL,
                params={
                    "origin": origin,
                    "destination": destination,
                    "departure_at": query.departure_date.isoformat(),
                    "one_way": "true",
                    "direct": str(query.direct_only).lower(),
                    "currency": "rub",
                    "market": "ru",
                    "sorting": "price",
                    "unique": "false",
                    "limit": 100,
                    "page": page,
                },
                headers={"X-Access-Token": self.token} if self.token else None,
            )
            try:
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPStatusError, ValueError):
                raise ProviderResponseError("Aviasales returned an HTTP error or invalid JSON") from None
            if not isinstance(payload, dict) or payload.get("success") is not True:
                raise ProviderResponseError("Aviasales returned an unsuccessful or malformed response")
            if not isinstance(payload.get("data"), list):
                raise ProviderResponseError("Aviasales returned an unsuccessful or malformed response")
            currency = payload.get("currency", "rub")
            if not isinstance(currency, str) or currency.lower() != "rub":
                raise ProviderResponseError("Expected RUB from Aviasales")
            for raw in payload["data"]:
                if not isinstance(raw, dict):
                    raise ProviderResponseError("Malformed Aviasales quote entry")
                if raw.get("return_at"):
                    raise ProviderResponseError("Aviasales returned a round-trip quote for a one-way request")
                item_currency = raw.get("currency", currency)
                if not isinstance(item_currency, str) or item_currency.lower() != "rub":
                    raise ProviderResponseError("Expected RUB from each Aviasales quote")
                try:
                    quote = FlightQuote.model_validate(
                        {
                            **raw,
                            "currency": "RUB",
                            "cache_mode": self.http.mode,
                            "cache_hit": response.extensions.get("cache_hit", False),
                            "recorded_at": response.extensions.get("recorded_at"),
                        }
                    )
                except ValidationError as exc:
                    raise ProviderResponseError("Malformed Aviasales one-way quote") from exc
                if quote.origin_airport != origin or quote.destination_airport != destination:
                    continue
                local_departure = provider_datetime(quote.departure_at.isoformat(), _airport(origin, self.routes_path))
                if local_departure.date() != query.departure_date or (query.direct_only and quote.transfers):
                    continue
                quotes.append(quote)
            if len(payload["data"]) < 100:
                return quotes
        raise ProviderResponseError("Aviasales pagination exceeded max_pages; results would be incomplete")


@dataclass(frozen=True)
class FlightSearchResult:
    flights: tuple[Flight, ...]
    quotes: tuple[FlightQuote, ...]
    provenance: tuple[dict[str, Any], ...]


class FlightTool:
    name = "search_flights"
    description = "Перелёты: демонстрационное расписание и тарифы на подтверждённой маршрутной сети."
    input_model = FlightSearch
    output_adapter = TypeAdapter(list[Flight])

    def __init__(
        self,
        *,
        mode: Literal["synthetic", "provider"] = "synthetic",
        seed: int = 42,
        http: FlightHttpClient | None = None,
        token: str | None = None,
        data_dir: Path | None = None,
    ):
        if mode not in ("synthetic", "provider"):
            raise ValueError("Unknown flight mode")
        self.mode, self.seed = mode, seed
        self.routes_path = resolve_data_dir(data_dir) / "routes/network.json"
        self.provider = AviasalesDataClient(http, token, routes_path=self.routes_path) if http is not None else None

    async def search_with_provenance(self, query: FlightSearch) -> FlightSearchResult:
        if self.mode == "synthetic":
            flights = generate_flights(query, seed=self.seed, routes_path=self.routes_path)
            route = _route(query.origin, query.destination, self.routes_path)
            return FlightSearchResult(
                tuple(flights),
                (),
                tuple(
                    {
                        "offer_id": item.id,
                        "schedule": "synthetic",
                        "prices": "synthetic",
                        "route_sources": list(route["sources"]) if route else [],
                        "seed": self.seed,
                        "pricing_version": PRICING_VERSION,
                        "infants": "under 2: 10% fare without seat; no standard baggage allowance",
                    }
                    for item in flights
                ),
            )
        if self.provider is None:
            raise ProviderNotConfigured("An injected HTTP client is required for provider flights")
        quotes = await self.provider.prices_for_dates(query)
        flights, provenance = [], []
        for quote in quotes:
            origin, destination = (
                _airport(quote.origin_airport, self.routes_path),
                _airport(quote.destination_airport, self.routes_path),
            )
            departure = provider_datetime(quote.departure_at.isoformat(), origin)
            route = _route(origin.iata, destination.iata, self.routes_path)
            if quote.duration_to is not None:
                duration, duration_source = quote.duration_to, "provider cached duration_to"
            elif route is not None:
                duration, duration_source = route["synthetic_duration_minutes"], "synthetic estimate"
            else:
                raise ProviderResponseError("No provider duration or modelled route duration")
            arrival = arrival_after(departure, duration, destination)
            price = group_fare(quote.price, query.travelers)
            if query.max_price_rub is not None and price > query.max_price_rub:
                continue
            identifier = stable_number(quote.model_dump(mode="json"), party_id(query.travelers), PRICING_VERSION)
            flight = Flight(
                id=f"provider-mixed-flight-{identifier:x}",
                origin=origin,
                destination=destination,
                departure_at=departure,
                arrival_at=arrival,
                price_rub=price,
                carrier=f"{quote.airline} (cached quote; synthetic group/extras)",
                stops=quote.transfers,
                baggage_price_rub=2000 * seated_count(query.travelers),
                is_overnight=arrival.date() > departure.date(),
                is_synthetic=True,
                price_source=FlightPriceSource(
                    seat_price_rub=quote.price,
                    cache_mode=quote.cache_mode,
                    cache_hit=quote.cache_hit,
                    recorded_at=quote.recorded_at,
                    duration_is_modelled=quote.duration_to is None,
                ),
            )
            flights.append(flight)
            provenance.append(
                {
                    "offer_id": flight.id,
                    "provider": "Aviasales Data API",
                    "provider_url": AVIASALES_URL,
                    "departure": "provider cached observation, not current availability",
                    "duration": duration_source,
                    "group_price": (
                        "single-seat cached quote × seated passengers + 10% per infant under 2 "
                        "without a seat + synthetic pet fee"
                    ),
                    "baggage": "synthetic 2000 RUB/seated passenger; no infant allowance",
                    "quote": quote.model_dump(mode="json"),
                }
            )
        return FlightSearchResult(tuple(flights), tuple(quotes), tuple(provenance))

    async def run(self, input: FlightSearch) -> list[Flight]:
        return list((await self.search_with_provenance(input)).flights)

    def public_metadata(self, result: list[Flight]) -> dict[str, Any]:
        if self.mode != "provider":
            return {"source": "synthetic"}
        return {
            "source": "aviasales_data_api",
            "cache_mode": self.provider.http.mode,
            "quotes": [item.price_source.model_dump(mode="json") for item in result if item.price_source],
        }
