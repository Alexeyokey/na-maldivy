"""Таймлайн поездки в местном времени (S3, #15).

Из варианта поездки собирается цепочка событий: вылет, прилёт, трансфер в отель, заселение, выезд,
трансфер в аэропорт, обратный рейс. Каждое событие — в местном времени своей точки. Между событиями
считаются ожидания: ночь в аэропорту в ожидании гидросамолёта, ранний приезд в отель до заселения,
хвост после выезда. По ним валидатор S5 (#24) ищет проблемы и предлагает исправления.

На вход идут объекты с полями как у контрактов D1 (#2): рейс как Journey, отель как HotelOffer.
Модели D1 подходят напрямую, без адаптеров. Трансферы берутся из базы S4 (app.tools.transfers).
"""

from datetime import date, datetime, time, timedelta
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, computed_field

from app.tools.transfers import (
    Hours,
    TransferBase,
    TransferQuote,
    Travelers,
    UnsupportedTransfer,
    default_base,
    quote_transfers,
)

AIRPORT_EXIT_MIN = 45  # паспортный контроль и багаж после прилёта
AIRPORT_BEFORE_DEPARTURE_MIN = 120  # быть в аэропорту до обратного вылета
MAX_LOOKBACK = timedelta(hours=48)  # дальше в прошлое трансфер в аэропорт не ищем

MODE_TITLES = {
    "taxi": "такси",
    "private_minivan": "минивэн",
    "shuttle": "шаттл",
    "metro": "метро",
    "speedboat": "катер",
    "seaplane": "гидросамолёт",
    "flight_speedboat": "местный рейс и катер",
}


class PlaceLike(Protocol):
    name: str
    timezone: str  # IANA


class JourneyLike(Protocol):
    id: str
    origin: PlaceLike
    destination: PlaceLike
    departure_at: datetime
    arrival_at: datetime


class StayLike(Protocol):
    hotel_id: str
    name: str
    place: PlaceLike
    check_in: date
    check_out: date
    check_in_time: time
    check_out_time: time


EventKind = Literal[
    "outbound_departure",
    "outbound_arrival",
    "arrival_transfer_departure",
    "arrival_transfer_arrival",
    "check_in",
    "check_out",
    "departure_transfer_departure",
    "departure_transfer_arrival",
    "return_departure",
    "return_arrival",
]
WaitKind = Literal[
    "airport_after_arrival",  # вышли из аэропорта, а трансфер ещё не идёт
    "hotel_before_check_in",  # приехали в отель раньше времени заселения
    "after_check_out",  # выселились, а трансфер в аэропорт позже
    "airport_before_departure",  # в аэропорту раньше, чем нужно к вылету
]


class TimelineEvent(BaseModel):
    kind: EventKind
    at: AwareDatetime  # местное время точки события
    place: str
    timezone: str
    title: str
    ref: str | None = None  # id рейса, отеля или варианта трансфера


class Wait(BaseModel):
    kind: WaitKind
    start: AwareDatetime
    end: AwareDatetime
    place: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def minutes(self) -> int:
        return round((self.end - self.start).total_seconds() / 60)


class TransferLeg(BaseModel):
    option_id: str
    mode: str
    depart_at: AwareDatetime
    arrive_at: AwareDatetime
    price_rub: int  # за всю группу
    issues: list[str] = []


class Timeline(BaseModel):
    events: list[TimelineEvent]
    waits: list[Wait]
    notes: list[str]
    booked_check_in: AwareDatetime  # заселение по брони: дата заезда и время заселения отеля
    booked_check_out: AwareDatetime
    hotel_arrival_at: AwareDatetime  # когда гость реально приезжает в отель
    hotel_departure_at: AwareDatetime | None  # когда уезжает, если известен обратный рейс

    def event(self, kind: EventKind) -> TimelineEvent | None:
        return next((e for e in self.events if e.kind == kind), None)

    def wait(self, kind: WaitKind) -> Wait | None:
        return next((w for w in self.waits if w.kind == kind), None)


def build_timeline(
    outbound: JourneyLike,
    stay: StayLike,
    inbound: JourneyLike | None = None,
    arrival_transfer: TransferLeg | None = None,
    departure_transfer: TransferLeg | None = None,
) -> Timeline:
    """Цепочка событий в логическом порядке. Если данные противоречат друг другу (трансфер раньше прилёта),
    порядок не исправляется: это ловит валидатор S5."""
    events: list[TimelineEvent] = []
    waits: list[Wait] = []
    notes: list[str] = []

    def add(kind: EventKind, at: datetime, place: PlaceLike | str, tz: str, title: str, ref: str | None) -> None:
        name = place if isinstance(place, str) else place.name
        events.append(
            TimelineEvent(kind=kind, at=at.astimezone(ZoneInfo(tz)), place=name, timezone=tz, title=title, ref=ref)
        )

    def wait(kind: WaitKind, start: datetime, end: datetime, place: str, tz: str) -> None:
        if end > start:
            zone = ZoneInfo(tz)
            waits.append(Wait(kind=kind, start=start.astimezone(zone), end=end.astimezone(zone), place=place))

    airport, hotel_tz = outbound.destination, stay.place.timezone
    add(
        "outbound_departure",
        outbound.departure_at,
        outbound.origin,
        outbound.origin.timezone,
        f"Вылет: {outbound.origin.name}",
        outbound.id,
    )
    add("outbound_arrival", outbound.arrival_at, airport, airport.timezone, f"Прилёт: {airport.name}", outbound.id)

    ready = outbound.arrival_at + timedelta(minutes=AIRPORT_EXIT_MIN)
    if arrival_transfer:
        wait("airport_after_arrival", ready, arrival_transfer.depart_at, airport.name, airport.timezone)
        add(
            "arrival_transfer_departure",
            arrival_transfer.depart_at,
            airport,
            airport.timezone,
            f"Трансфер в отель: {MODE_TITLES.get(arrival_transfer.mode, arrival_transfer.mode)}",
            arrival_transfer.option_id,
        )
        add(
            "arrival_transfer_arrival",
            arrival_transfer.arrive_at,
            stay.name,
            hotel_tz,
            f"Приезд в отель: {stay.name}",
            arrival_transfer.option_id,
        )
        hotel_arrival = arrival_transfer.arrive_at
    else:
        notes.append("Нет трансфера из аэропорта: приезд в отель считается сразу после выхода из аэропорта")
        hotel_arrival = ready

    zone = ZoneInfo(hotel_tz)
    booked_check_in = datetime.combine(stay.check_in, stay.check_in_time, tzinfo=zone)
    booked_check_out = datetime.combine(stay.check_out, stay.check_out_time, tzinfo=zone)
    wait("hotel_before_check_in", hotel_arrival, booked_check_in, stay.name, hotel_tz)
    add("check_in", max(hotel_arrival, booked_check_in), stay.name, hotel_tz, f"Заселение: {stay.name}", stay.hotel_id)

    hotel_departure: datetime | None = None
    if inbound is None:
        add("check_out", booked_check_out, stay.name, hotel_tz, f"Выезд: {stay.name}", stay.hotel_id)
    else:
        deadline = inbound.departure_at - timedelta(minutes=AIRPORT_BEFORE_DEPARTURE_MIN)
        if departure_transfer:
            hotel_departure = departure_transfer.depart_at
        else:
            notes.append("Нет трансфера в аэропорт: выезд считается впритык к сроку приезда в аэропорт")
            hotel_departure = deadline
        add(
            "check_out",
            min(hotel_departure, booked_check_out),
            stay.name,
            hotel_tz,
            f"Выезд: {stay.name}",
            stay.hotel_id,
        )
        wait("after_check_out", booked_check_out, hotel_departure, stay.name, hotel_tz)
        if departure_transfer:
            add(
                "departure_transfer_departure",
                departure_transfer.depart_at,
                stay.name,
                hotel_tz,
                f"Трансфер в аэропорт: {MODE_TITLES.get(departure_transfer.mode, departure_transfer.mode)}",
                departure_transfer.option_id,
            )
            add(
                "departure_transfer_arrival",
                departure_transfer.arrive_at,
                inbound.origin,
                inbound.origin.timezone,
                f"Приезд в аэропорт: {inbound.origin.name}",
                departure_transfer.option_id,
            )
            wait(
                "airport_before_departure",
                departure_transfer.arrive_at,
                deadline,
                inbound.origin.name,
                inbound.origin.timezone,
            )
        add(
            "return_departure",
            inbound.departure_at,
            inbound.origin,
            inbound.origin.timezone,
            f"Обратный вылет: {inbound.origin.name}",
            inbound.id,
        )
        add(
            "return_arrival",
            inbound.arrival_at,
            inbound.destination,
            inbound.destination.timezone,
            f"Прилёт домой: {inbound.destination.name}",
            inbound.id,
        )

    return Timeline(
        events=events,
        waits=waits,
        notes=notes,
        booked_check_in=booked_check_in,
        booked_check_out=booked_check_out,
        hotel_arrival_at=hotel_arrival.astimezone(zone),
        hotel_departure_at=hotel_departure.astimezone(zone) if hotel_departure else None,
    )


def _leg(quote: TransferQuote) -> TransferLeg:
    return TransferLeg(
        option_id=quote.option_id,
        mode=quote.mode,
        depart_at=quote.depart_at,
        arrive_at=quote.arrive_at,
        price_rub=quote.price_rub,
        issues=list(quote.issues),
    )


def arrival_transfer_leg(
    hotel_id: str,
    airport: str,
    flight_arrival_at: datetime,
    travelers: Travelers,
    base: TransferBase | None = None,
) -> TransferLeg | None:
    """Лучший трансфер из аэропорта: готовы ехать через AIRPORT_EXIT_MIN после посадки.
    None, если отеля нет в базе S4: тогда таймлайн строится без трансфера и с пометкой."""
    pickup = flight_arrival_at + timedelta(minutes=AIRPORT_EXIT_MIN)
    try:
        quotes = quote_transfers(hotel_id, airport, pickup, travelers, base=base)
    except UnsupportedTransfer:
        return None
    return _leg(quotes[0]) if quotes else None


def departure_transfer_leg(
    hotel_id: str,
    airport: str,
    flight_departure_at: datetime,
    travelers: Travelers,
    base: TransferBase | None = None,
) -> TransferLeg | None:
    """Трансфер в аэропорт к обратному вылету: самое позднее отправление, при котором успеваем за
    AIRPORT_BEFORE_DEPARTURE_MIN до вылета. В базе S4 только направление аэропорт → отель, поэтому обратный путь
    считается по тем же вариантам: то же время в пути и то же расписание."""
    try:
        record = (base or default_base()).get(hotel_id, airport)
    except UnsupportedTransfer:
        return None
    zone = ZoneInfo(record.tz)
    deadline = (flight_departure_at - timedelta(minutes=AIRPORT_BEFORE_DEPARTURE_MIN)).astimezone(zone)
    best: tuple[bool, float, int, TransferLeg] | None = None
    for option in record.options:
        depart = _latest_departure(option.hours, deadline - timedelta(minutes=option.duration_min))
        if depart is None:
            continue
        quote = next(
            q for q in quote_transfers(hotel_id, airport, depart, travelers, base=base) if q.option_id == option.id
        )
        extra_wait = (deadline - quote.arrive_at).total_seconds()
        key = ("pets_forbidden" in quote.issues, extra_wait, quote.price_rub, _leg(quote))
        if best is None or key[:3] < best[:3]:
            best = key
    return best[3] if best else None


def _latest_departure(hours: Hours | None, target: datetime) -> datetime | None:
    """Самый поздний момент не позже target, когда вариант ходит. Шаг — минута."""
    if hours is None or hours.contains(target.time()):
        return target
    moment = target.replace(second=0, microsecond=0)
    limit = target - MAX_LOOKBACK
    while moment > limit:
        moment -= timedelta(minutes=1)
        if hours.contains(moment.time()):
            return moment
    return None
