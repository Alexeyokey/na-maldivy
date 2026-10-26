"""Конечный синтетический каталог D1; сеть, ключи и текущее время не нужны.

MOW в поиске перелётов означает аэропорт SVO; для поездов SVO означает город
MOW. Цены перелёта и поезда включают всех людей и доплату за животных, отеля —
все ночи и номера (один номер на двух человек). Багаж у перелёта — отдельная
цена за группу. Климат, тарифы и визовые признаки здесь демонстрационные.
"""

from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from math import asin, ceil, cos, radians, sin, sqrt
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, TypeAdapter

from app.contracts.tools import (
    DayPrice,
    Destination,
    DestinationQuery,
    Flight,
    FlightSearch,
    HotelOffer,
    HotelSearch,
    Place,
    PriceCalendarQuery,
    Train,
    TrainSearch,
    Transfer,
    TransferHours,
    TransferQuery,
    TransferRequest,
    Travelers,
)
from app.tools.base import Tool
from app.tools.errors import UnsupportedTransfer

# Только неизменяемые значения: каждому вызову выдаются новые Pydantic-объекты.
_PLACES = {
    "SVO": ("Шереметьево", 55.9726, 37.4146, "Europe/Moscow"),
    "MOW": ("Москва", 55.7558, 37.6173, "Europe/Moscow"),
    "LED": ("Санкт-Петербург", 59.8003, 30.2625, "Europe/Moscow"),
    "AYT": ("Анталья", 36.8987, 30.8005, "Europe/Istanbul"),
    "DXB": ("Дубай", 25.2532, 55.3657, "Asia/Dubai"),
    "MLE": ("Мале", 4.1918, 73.5291, "Indian/Maldives"),
}
_DESTINATIONS = ("AYT", "DXB", "MLE")
_FARES = {"AYT": 18_000, "DXB": 26_000, "MLE": 42_000}
_DURATIONS = {"AYT": 270, "DXB": 330, "MLE": 540}
_HOTEL_NIGHT = {"AYT": 4_000, "DXB": 7_000, "MLE": 12_000}
# Месяцы январь..декабрь; это фиксированные демонстрационные значения.
_CLIMATE = {
    "AYT": (
        (15, 16, 18, 22, 26, 31, 34, 34, 31, 26, 21, 17),
        (17, 17, 17, 18, 21, 25, 28, 29, 27, 25, 22, 19),
    ),
    "DXB": (
        (24, 25, 28, 33, 38, 40, 42, 42, 39, 35, 30, 26),
        (22, 22, 23, 26, 29, 32, 33, 34, 33, 30, 27, 24),
    ),
    "MLE": (
        (29, 29, 30, 31, 30, 29, 29, 29, 29, 29, 29, 29),
        (28, 28, 29, 30, 29, 29, 28, 28, 28, 28, 28, 28),
    ),
}


def _place(code: str) -> Place:
    name, lat, lon, tz = _PLACES[code]
    return Place(
        kind="city" if code == "MOW" else "airport",
        iata=code,
        name=name,
        latitude=lat,
        longitude=lon,
        timezone=tz,
    )


def _group_price(per_person: int, travelers: Travelers, pet_fee: int = 1_500) -> int:
    # Возрастного тарифа в D1 нет: каждое указанное лицо занимает одно место.
    return per_person * travelers.count + pet_fee * travelers.pets


def _group_id(travelers: Travelers) -> str:
    children = ".".join(map(str, travelers.children_ages)) or "none"
    return f"a{travelers.adults}-c{children}-p{travelers.pets}"


def _flight_route(origin: str, destination: str) -> tuple[str, str, str] | None:
    origin = "SVO" if origin == "MOW" else origin
    destination = "SVO" if destination == "MOW" else destination
    if origin in ("SVO", "LED") and destination in _DESTINATIONS:
        return origin, destination, destination
    if destination in ("SVO", "LED") and origin in _DESTINATIONS:
        return origin, destination, origin
    return None


def _flight_fare(route: tuple[str, str, str], day: date) -> int:
    origin, destination, resort = route
    return _FARES[resort] + (2_000 if "LED" in (origin, destination) else 0) + (day.day % 7) * 250


def _flights(query: FlightSearch) -> list[Flight]:
    route = _flight_route(query.origin, query.destination)
    if route is None:
        return []
    origin_code, destination_code, resort = route
    origin, destination = _place(origin_code), _place(destination_code)
    duration = _DURATIONS[resort] + (60 if "LED" in route[:2] else 0)
    flights = []
    # 23:30 — намеренная ловушка: прибытие происходит на следующую местную дату.
    # Например, SVO 2030-11-10 23:30+03 -> DXB 2030-11-11 06:00+04.
    for index, departure_time in enumerate((time(9), time(23, 30))):
        departure = datetime.combine(query.departure_date, departure_time, ZoneInfo(origin.timezone))
        try:
            arrival_utc = departure.astimezone(UTC) + timedelta(minutes=duration)
            arrival = arrival_utc.astimezone(ZoneInfo(destination.timezone))
        except OverflowError:
            # У date.max поздний рейс уже не имеет представимого времени прибытия.
            continue
        price = _group_price(_flight_fare(route, query.departure_date) + index * 1_000, query.travelers)
        if query.max_price_rub is not None and price > query.max_price_rub:
            continue
        flights.append(
            Flight(
                id=f"stub-flight-{origin_code}-{destination_code}-{query.departure_date}-{index}-{_group_id(query.travelers)}",
                origin=origin.model_copy(deep=True),
                destination=destination.model_copy(deep=True),
                departure_at=departure,
                arrival_at=arrival,
                price_rub=price,
                carrier="Demo Air (synthetic)",
                stops=0,
                baggage_price_rub=2_000 * query.travelers.count,
                is_overnight=arrival.date() > departure.date(),
                is_synthetic=True,
            )
        )
    return flights


# suffix, stars, price coefficient /10, adults only, pets, refundable.
_HOTEL_TYPES = (
    ("budget", 3, 8, False, False, False),
    ("family", 4, 12, False, True, True),
    ("adults", 5, 10, True, False, True),
    ("premium", 5, 20, False, True, True),
)


def _hotel_place(code: str, index: int) -> Place:
    place = _place(code)
    # Фиксированные точки вблизи аэропорта, включая остров Мале.
    return place.model_copy(
        update={
            "kind": "hotel",
            "id": _hotel_id(code, index),
            "name": f"Demo {code} {_HOTEL_TYPES[index][0]} hotel",
            "latitude": place.latitude + (index + 1) * 0.002,
            "longitude": place.longitude + (index + 1) * 0.002,
        }
    )


def _hotel_id(code: str, index: int) -> str:
    return f"demo-{code.lower()}-{_HOTEL_TYPES[index][0]}"


def _hotels(query: HotelSearch) -> list[HotelOffer]:
    if query.destination not in _DESTINATIONS:
        return []
    nights = (query.check_out - query.check_in).days
    rooms = ceil(query.travelers.count / 2)
    offers = []
    for index, (suffix, stars, coefficient, adults_only, pets_allowed, refundable) in enumerate(_HOTEL_TYPES):
        if stars < query.min_stars or (query.refundable_only and not refundable):
            continue
        if (adults_only and query.travelers.children_ages) or (query.travelers.pets and not pets_allowed):
            continue
        price = (_HOTEL_NIGHT[query.destination] * coefficient // 10 * rooms + 700 * query.travelers.pets) * nights
        if query.max_price_rub is not None and price > query.max_price_rub:
            continue
        offers.append(
            HotelOffer(
                hotel_id=_hotel_id(query.destination, index),
                offer_id=(
                    f"stub-hotel-{query.destination}-{suffix}-{query.check_in}-{query.check_out}"
                    f"-{_group_id(query.travelers)}"
                ),
                name=f"Demo {query.destination} {suffix}",
                place=_hotel_place(query.destination, index),
                check_in=query.check_in,
                check_out=query.check_out,
                price_rub=price,
                stars=stars,
                rating=7.5 + index * 0.5,
                adults_only=adults_only,
                pets_allowed=pets_allowed,
                refundable=refundable,
                check_in_time=time(14),
                check_out_time=time(12),
                reception_24h=suffix != "budget",
                reception_closes_at=time(22) if suffix == "budget" else None,
                early_check_in_available=suffix != "budget",
                early_check_in_price_rub=None if suffix == "budget" else 1_200 * rooms,
                is_synthetic=True,
            )
        )
    return offers


def _trains(query: TrainSearch) -> list[Train]:
    origin_code = "MOW" if query.origin == "SVO" else query.origin
    destination_code = "MOW" if query.destination == "SVO" else query.destination
    if {origin_code, destination_code} != {"MOW", "LED"}:
        return []
    origin = _place(origin_code)
    destination = _place(destination_code)
    # Железнодорожная точка LED — город, а не аэропорт Пулково.
    for place in (origin, destination):
        place.kind = "city"
        if place.iata == "LED":
            place.latitude, place.longitude = 59.9301, 30.3619
            place.name = "Санкт-Петербург"
    fares = {"seated": 3_500, "platzkart": 4_500, "coupe": 7_000, "sv": 15_000}
    departure = datetime.combine(query.departure_date, time(23, 55), ZoneInfo(origin.timezone))
    try:
        arrival = (departure.astimezone(UTC) + timedelta(hours=8)).astimezone(ZoneInfo(destination.timezone))
    except OverflowError:
        return []
    return [
        Train(
            id=f"stub-train-{origin_code}-{destination_code}-{query.departure_date}-{query.seat_class}-{_group_id(query.travelers)}",
            origin=origin,
            destination=destination,
            departure_at=departure,
            arrival_at=arrival,
            number="DEMO-001" if origin_code == "MOW" else "DEMO-002",
            seat_class=query.seat_class,
            price_rub=_group_price(fares[query.seat_class], query.travelers, pet_fee=500),
            is_synthetic=True,
        )
    ]


def _demo_transfer_point(place: Place) -> bool:
    if place.iata not in _PLACES:
        return False
    points = [_place(place.iata)]
    if place.iata in _DESTINATIONS:
        points.extend(_hotel_place(place.iata, index) for index in range(len(_HOTEL_TYPES)))
    if place.iata == "LED":
        points.append(_place("LED").model_copy(update={"kind": "city", "latitude": 59.9301, "longitude": 30.3619}))
    return any(
        abs(place.latitude - point.latitude) < 1e-7
        and abs(place.longitude - point.longitude) < 1e-7
        and place.timezone == point.timezone
        and place.kind == point.kind
        and place.id == point.id
        for point in points
    )


def _within_hours(value: time, hours: TransferHours) -> bool:
    """Local civil service windows are half-open, including overnight windows."""
    if hours.from_ < hours.to:
        return hours.from_ <= value < hours.to
    return value >= hours.from_ or value < hours.to


def _next_departure(pickup: datetime, timezone: str, hours: TransferHours | None) -> tuple[bool, datetime | None]:
    """Immediate or earliest valid opening; None hours means known 24/7.

    UTC round trips reject nonexistent local times. For a DST gap containing an
    opening, seek the first real minute inside the window; both folds are checked
    on an autumn transition. Stub schedules have minute precision.
    """
    zone = ZoneInfo(timezone)
    local = pickup.astimezone(zone)
    wall_time = local.timetz().replace(tzinfo=None)
    if hours is None or _within_hours(wall_time, hours):
        return True, pickup
    requested_utc = pickup.astimezone(UTC)
    for offset in range(3):
        try:
            day = local.date() + timedelta(days=offset)
            opening = datetime.combine(day, hours.from_)
        except OverflowError:
            break
        candidates = []
        for minute in range(181):
            try:
                naive = opening + timedelta(minutes=minute)
            except OverflowError:
                break
            for fold in (0, 1):
                candidate = naive.replace(tzinfo=zone, fold=fold)
                try:
                    utc = candidate.astimezone(UTC)
                    returned = utc.astimezone(zone)
                except OverflowError:
                    continue
                if returned.replace(tzinfo=None) != naive or returned.fold != fold:
                    continue
                if utc >= requested_utc and _within_hours(naive.time(), hours):
                    candidates.append(candidate)
            if candidates:
                return False, min(candidates, key=lambda value: value.astimezone(UTC))
    return False, None


def _transfer(query: TransferQuery) -> list[Transfer]:
    # Internal callers may hold a copied DTO; revalidate before using places or
    # instants so every returned pickup is normalized to the origin's zone.
    query = TransferQuery.model_validate(query)
    codes = {query.origin.iata, query.destination.iata}
    same_region = len(codes) == 1 or codes == {"SVO", "MOW"}
    if not same_region or not all(_demo_transfer_point(place) for place in (query.origin, query.destination)):
        raise UnsupportedTransfer("Unsupported demo transfer route: use known demo airport/city/hotel points")
    lat1, lat2 = map(radians, (query.origin.latitude, query.destination.latitude))
    delta_lat = lat2 - lat1
    delta_lon = radians(query.destination.longitude - query.origin.longitude)
    distance = 6_371 * 2 * asin(min(1, sqrt(sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2)))
    if distance < 0.001:
        raise UnsupportedTransfer("Unsupported demo transfer route: origin and destination are the same point")
    road_distance = distance * 1.4  # Демонстрационный коэффициент дорожного маршрута.
    local_pickup = query.pickup_at.astimezone(ZoneInfo(query.origin.timezone))
    billable_passengers = query.travelers.adults + sum(age >= 2 for age in query.travelers.children_ages)
    alternatives = []

    def add(
        mode: str,
        price: int,
        minutes: int,
        hours: TransferHours | None,
        vehicles: int | None = None,
        pets: bool | None = None,
        child_seat: bool | None = None,
        luggage_limit: str | None = None,
        prebook_hours: int | None = 0,
    ) -> None:
        available, departure = _next_departure(query.pickup_at, query.origin.timezone, hours)
        issues = []
        if not available:
            issues.append("unavailable_at_pickup")
        if vehicles is not None and vehicles > 1:
            issues.append("extra_vehicle")
        if mode in {"taxi", "minivan"} and (local_pickup.hour >= 22 or local_pickup.hour < 6):
            issues.append("night_surcharge")
        if query.travelers.pets:
            if pets is False:
                issues.append("pets_forbidden")
            elif pets is None:
                issues.append("pets_unknown")
        if mode in {"taxi", "minivan"} and any(age < 12 for age in query.travelers.children_ages):
            if child_seat is False:
                issues.append("child_seat_missing")
            elif child_seat is None:
                issues.append("child_seat_unknown")
        if minutes > 90:
            issues.append("long_transfer")
        if prebook_hours is not None and prebook_hours >= 24:
            issues.append("prebook_required")
        if luggage_limit:
            issues.append("luggage_limit")
        alternatives.append(
            Transfer(
                id=(
                    f"stub-transfer-v2-{mode}-{query.origin.kind}-{query.origin.id or query.origin.iata}"
                    f"-{query.origin.latitude}-{query.origin.longitude}"
                    f"-{query.destination.kind}-{query.destination.id or query.destination.iata}"
                    f"-{query.destination.latitude}-{query.destination.longitude}"
                    f"-{query.pickup_at.isoformat()}-{_group_id(query.travelers)}"
                ),
                origin=query.origin.model_copy(deep=True),
                destination=query.destination.model_copy(deep=True),
                pickup_at=query.pickup_at,
                depart_at=departure,
                duration_minutes=minutes,
                price_rub=price,
                mode=mode,
                vehicles=vehicles,
                hours=hours,
                available=available,
                pets=pets,
                child_seat=child_seat,
                luggage_limit=luggage_limit,
                prebook_hours=prebook_hours,
                distance_km=round(float(distance if mode in {"boat", "seaplane"} else road_distance), 3),
                issues=issues,
                is_synthetic=True,
            )
        )

    if codes == {"MLE"}:
        # Synthetic island graph: public fares per paying passenger, infants <2 free.
        add(
            "boat",
            billable_passengers * 4000,
            max(20, ceil(distance / 25 * 60)),
            None,
            pets=None,
            child_seat=None,
            luggage_limit="Демо: одна сумка на пассажира",
            prebook_hours=2,
        )
        add(
            "seaplane",
            billable_passengers * 15000,
            max(15, ceil(distance / 180 * 60)),
            TransferHours(from_=time(6), to=time(18)),
            pets=False,
            child_seat=None,
            luggage_limit="Демо: багаж до 20 кг на оплаченного пассажира",
            prebook_hours=24,
        )
    else:
        night_coefficient = 125 if local_pickup.hour >= 22 or local_pickup.hour < 6 else 100
        taxis = (query.travelers.count + 3) // 4  # Capacity counts every human, including infants.
        minivans = (query.travelers.count + 5) // 6
        taxi_price = (500 + ceil(road_distance) * 80) * taxis + query.travelers.pets * 300
        van_price = (750 + ceil(road_distance) * 110) * minivans + query.travelers.pets * 300
        duration = max(5, ceil(road_distance / 35 * 60))
        add(
            "taxi",
            (taxi_price * night_coefficient + 99) // 100,
            duration,
            None,
            taxis,
            pets=True,
            child_seat=None,
        )
        add(
            "minivan",
            (van_price * night_coefficient + 99) // 100,
            duration,
            None,
            minivans,
            pets=True,
            child_seat=True,
            prebook_hours=2,
        )
        add(
            "shuttle",
            billable_passengers * 600,
            max(15, duration + 10),
            TransferHours(from_=time(6), to=time(22)),
            pets=None,
            child_seat=None,
            luggage_limit="Демо: один стандартный чемодан на пассажира",
            prebook_hours=24,
        )
        add(
            "bus",
            billable_passengers * 200,
            max(20, duration + 20),
            TransferHours(from_=time(6), to=time(23, 30)),
            pets=False,
            child_seat=None,
        )
        if codes == {"DXB"}:
            add(
                "metro",
                billable_passengers * 250,
                max(15, duration + 15),
                TransferHours(from_=time(5), to=time(1)),
                pets=False,
                child_seat=None,
            )
    blocking = {"pets_forbidden", "child_seat_missing"}
    return sorted(
        alternatives,
        key=lambda item: (
            not item.available,
            bool(blocking.intersection(item.issues)),
            item.price_rub,
            item.id,
        ),
    )


def _public_transfer(request: TransferRequest) -> list[Transfer]:
    """Resolve physical points on the server; user-supplied coordinates are not accepted."""
    hotel = next(
        (
            _hotel_place(code, index)
            for code in _DESTINATIONS
            for index in range(len(_HOTEL_TYPES))
            if _hotel_id(code, index) == request.hotel_id
        ),
        None,
    )
    if hotel is None or request.airport not in _DESTINATIONS or hotel.iata != request.airport:
        raise UnsupportedTransfer("Unsupported demo transfer route: airport and known hotel ID must match")
    return _transfer(
        TransferQuery(
            origin=_place(request.airport),
            destination=hotel,
            pickup_at=request.pickup_at,
            travelers=request.travelers,
        )
    )


def _destinations(query: DestinationQuery) -> list[Destination]:
    suggestions = []
    for code in _DESTINATIONS:
        route = _flight_route(query.origin, code)
        if route is None:
            continue
        air, water = _CLIMATE[code]
        water_temperature = water[query.month - 1]
        if query.min_water_temperature_c is not None and water_temperature < query.min_water_temperature_c:
            continue
        # Для D1 все три направления синтетически безвизовые; это не визовая справка.
        visa_required = False
        if query.visa_free_only and visa_required:
            continue
        flights = 2 * _group_price(_flight_fare(route, date(2030, query.month, 15)), query.travelers)
        hotel = (
            _HOTEL_NIGHT[code] * 12 // 10 * ceil(query.travelers.count / 2) + query.travelers.pets * 700
        ) * query.nights
        total = flights + hotel
        if query.budget_rub is not None and total > query.budget_rub:
            continue
        risks = ["Демонстрационные климат, цены и визовые условия; требуется проверка реальных источников"]
        if code == "AYT" and water_temperature < 24:
            risks.append("Вода прохладнее 24 °C")
        if code == "MLE" and query.month in range(5, 11):
            risks.append("Демонстрационный сезон дождей")
        suggestions.append(
            Destination(
                place=_place(code),
                country={"AYT": "Турция", "DXB": "ОАЭ", "MLE": "Мальдивы"}[code],
                month=query.month,
                air_temperature_c=air[query.month - 1],
                water_temperature_c=water_temperature,
                flight_duration_minutes=_DURATIONS[code] + (60 if route[0] == "LED" else 0),
                visa_required=visa_required,
                estimated_total_price_rub=total,
                tags=["море", "synthetic"],
                risks=risks,
                is_synthetic=True,
            )
        )
    return sorted(suggestions, key=lambda destination: destination.estimated_total_price_rub)


def _calendar(query: PriceCalendarQuery) -> list[DayPrice]:
    if _flight_route(query.origin, query.destination) is None:
        return []
    year, month = map(int, query.month.split("-"))
    prices = []
    for day in range(1, monthrange(year, month)[1] + 1):
        departure_date = date(year, month, day)
        flights = _flights(
            FlightSearch(
                origin=query.origin,
                destination=query.destination,
                departure_date=departure_date,
                travelers=query.travelers,
            )
        )
        prices.append(DayPrice(date=departure_date, price_rub=min(f.price_rub for f in flights), is_synthetic=True))
    return prices


@dataclass
class StubTool[Input: BaseModel, Output]:
    name: str
    description: str
    input_model: type[Input]
    output_adapter: TypeAdapter[Output]
    handler: Callable[[Input], Output]

    async def run(self, input: Input) -> Output:
        return self.handler(input)


def create_stub_tools() -> dict[str, Tool[Any, Any]]:
    """Новые экземпляры инструментов с единым интерфейсом будущих адаптеров D2–D8."""
    tools: list[Tool[Any, Any]] = [
        StubTool(
            "search_flights",
            "Синтетические перелёты SVO/LED ↔ AYT/DXB/MLE",
            FlightSearch,
            TypeAdapter(list[Flight]),
            _flights,
        ),
        StubTool(
            "search_hotels",
            "Синтетические отели AYT/DXB/MLE за весь срок",
            HotelSearch,
            TypeAdapter(list[HotelOffer]),
            _hotels,
        ),
        StubTool(
            "search_trains",
            "Синтетические поезда Москва ↔ Санкт-Петербург",
            TrainSearch,
            TypeAdapter(list[Train]),
            _trains,
        ),
        StubTool(
            "get_transfer",
            "Синтетический трансфер только между точками демо-каталога",
            TransferRequest,
            TypeAdapter(list[Transfer]),
            _public_transfer,
        ),
        StubTool(
            "suggest_destinations",
            "Синтетический справочник трёх направлений по месяцу",
            DestinationQuery,
            TypeAdapter(list[Destination]),
            _destinations,
        ),
        StubTool(
            "price_calendar",
            "Синтетические минимальные цены перелётов на каждый день месяца",
            PriceCalendarQuery,
            TypeAdapter(list[DayPrice]),
            _calendar,
        ),
    ]
    return {tool.name: tool for tool in tools}
