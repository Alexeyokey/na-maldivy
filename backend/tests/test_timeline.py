from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.booking.timeline import (
    arrival_transfer_leg,
    build_timeline,
    departure_transfer_leg,
)
from app.tools.transfers import Hours, Travelers

MSK = ZoneInfo("Europe/Moscow")
DUBAI = ZoneInfo("Asia/Dubai")
MALE = ZoneInfo("Indian/Maldives")
ANTALYA = ZoneInfo("Europe/Istanbul")

CONRAD = "osm:node/4455235191"  # Мальдивы, только гидросамолёт 06:00–15:30, 89 минут
KAYA_BELEK = "osm:node/897383953"  # Анталья: такси, минивэн, шаттл 06:00–23:00 за 77 минут


@dataclass
class Place:
    name: str
    timezone: str


@dataclass
class Journey:
    id: str
    origin: Place
    destination: Place
    departure_at: datetime
    arrival_at: datetime


@dataclass
class Stay:
    hotel_id: str
    name: str
    place: Place
    check_in: date
    check_out: date
    check_in_time: time = time(14)
    check_out_time: time = time(12)


SVO = Place("Шереметьево", "Europe/Moscow")
DXB = Place("Dubai International Airport", "Asia/Dubai")
MLE = Place("Velana International Airport", "Indian/Maldives")
AYT = Place("Antalya Airport", "Europe/Istanbul")
NIGHT_TO_DUBAI = Journey(
    "f-out", SVO, DXB, datetime(2026, 11, 18, 23, 30, tzinfo=MSK), datetime(2026, 11, 19, 6, 0, tzinfo=DUBAI)
)


def dubai_stay(check_in: date) -> Stay:
    return Stay("osm:way/126601133", "Al Manzil", Place("Al Manzil", "Asia/Dubai"), check_in, date(2026, 11, 25))


def test_night_flight_shifts_check_in_to_next_day() -> None:
    timeline = build_timeline(NIGHT_TO_DUBAI, dubai_stay(date(2026, 11, 19)))

    assert timeline.event("outbound_departure").at.isoformat() == "2026-11-18T23:30:00+03:00"
    assert timeline.event("outbound_arrival").at.isoformat() == "2026-11-19T06:00:00+04:00"
    assert timeline.hotel_arrival_at == datetime(2026, 11, 19, 6, 45, tzinfo=DUBAI)
    assert timeline.event("check_in").at == datetime(2026, 11, 19, 14, 0, tzinfo=DUBAI)
    wait = timeline.wait("hotel_before_check_in")
    assert (wait.start, wait.end, wait.minutes) == (timeline.hotel_arrival_at, timeline.booked_check_in, 435)
    assert any("Нет трансфера из аэропорта" in note for note in timeline.notes)


def test_booked_from_previous_day_checks_in_on_arrival() -> None:
    timeline = build_timeline(NIGHT_TO_DUBAI, dubai_stay(date(2026, 11, 18)))

    assert timeline.event("check_in").at == datetime(2026, 11, 19, 6, 45, tzinfo=DUBAI)
    assert timeline.wait("hotel_before_check_in") is None
    # Первая ночь оплачена, но гость приезжает только утром — это ловит валидатор S5
    assert timeline.booked_check_in.date() < timeline.hotel_arrival_at.date()


def test_each_event_in_local_time_and_return_crosses_midnight() -> None:
    inbound = Journey(
        "f-back", DXB, SVO, datetime(2026, 11, 25, 23, 50, tzinfo=DUBAI), datetime(2026, 11, 26, 3, 50, tzinfo=MSK)
    )

    timeline = build_timeline(NIGHT_TO_DUBAI, dubai_stay(date(2026, 11, 19)), inbound)

    assert [e.kind for e in timeline.events] == [
        "outbound_departure",
        "outbound_arrival",
        "check_in",
        "check_out",
        "return_departure",
        "return_arrival",
    ]
    assert timeline.event("return_departure").at.isoformat() == "2026-11-25T23:50:00+04:00"
    assert timeline.event("return_arrival").at.isoformat() == "2026-11-26T03:50:00+03:00"
    # Без трансфера уезжаем к сроку «за 2 часа до вылета»: 21:50, а выезд из отеля в 12:00
    assert timeline.hotel_departure_at == datetime(2026, 11, 25, 21, 50, tzinfo=DUBAI)
    assert timeline.event("check_out").at == datetime(2026, 11, 25, 12, 0, tzinfo=DUBAI)
    assert timeline.wait("after_check_out").minutes == 590


def test_maldives_night_arrival_waits_for_seaplane() -> None:
    outbound = Journey(
        "f-mle", SVO, MLE, datetime(2026, 11, 18, 14, 10, tzinfo=MSK), datetime(2026, 11, 18, 22, 55, tzinfo=MALE)
    )
    stay = Stay(
        CONRAD, "Conrad Rangali", Place("Conrad Rangali", "Indian/Maldives"), date(2026, 11, 19), date(2026, 11, 24)
    )

    leg = arrival_transfer_leg(CONRAD, "MLE", outbound.arrival_at, Travelers(adults=2))
    timeline = build_timeline(outbound, stay, arrival_transfer=leg)

    assert leg.mode == "seaplane"
    assert leg.depart_at.isoformat() == "2026-11-19T06:00:00+05:00"
    assert leg.arrive_at.isoformat() == "2026-11-19T07:29:00+05:00"
    assert leg.price_rub == 66000
    assert "unavailable_at_pickup" in leg.issues
    airport_wait = timeline.wait("airport_after_arrival")
    assert airport_wait.start.isoformat() == "2026-11-18T23:40:00+05:00"
    assert airport_wait.minutes == 380
    assert timeline.wait("hotel_before_check_in").minutes == 391
    assert timeline.event("arrival_transfer_departure").title == "Трансфер в отель: гидросамолёт"


def test_departure_leg_takes_last_seaplane_before_night_flight() -> None:
    outbound = Journey(
        "f-mle", SVO, MLE, datetime(2026, 11, 18, 2, 0, tzinfo=MSK), datetime(2026, 11, 18, 10, 45, tzinfo=MALE)
    )
    inbound = Journey(
        "f-back", MLE, SVO, datetime(2026, 11, 25, 2, 0, tzinfo=MALE), datetime(2026, 11, 25, 8, 30, tzinfo=MSK)
    )
    stay = Stay(
        CONRAD, "Conrad Rangali", Place("Conrad Rangali", "Indian/Maldives"), date(2026, 11, 18), date(2026, 11, 24)
    )

    leg = departure_transfer_leg(CONRAD, "MLE", inbound.departure_at, Travelers(adults=2))
    timeline = build_timeline(outbound, stay, inbound, departure_transfer=leg)

    # Нужно быть в аэропорту к 00:00, а последний гидросамолёт уходит до 15:30
    assert leg.depart_at.isoformat() == "2026-11-24T15:29:00+05:00"
    assert leg.arrive_at.isoformat() == "2026-11-24T16:58:00+05:00"
    assert leg.price_rub == 66000
    assert timeline.wait("after_check_out").minutes == 209
    assert timeline.wait("airport_before_departure").minutes == 422
    assert timeline.event("check_out").at == datetime(2026, 11, 24, 12, 0, tzinfo=MALE)


def test_antalya_transfers_both_ways() -> None:
    arrival = datetime(2026, 11, 18, 14, 0, tzinfo=ANTALYA)
    departure = datetime(2026, 11, 25, 10, 0, tzinfo=ANTALYA)

    there = arrival_transfer_leg(KAYA_BELEK, "AYT", arrival, Travelers(adults=2))
    back = departure_transfer_leg(KAYA_BELEK, "AYT", departure, Travelers(adults=2))
    back_with_dog = departure_transfer_leg(KAYA_BELEK, "AYT", departure, Travelers(adults=2, pets=1))

    assert (there.mode, there.depart_at.isoformat(), there.arrive_at.isoformat(), there.price_rub) == (
        "shuttle",
        "2026-11-18T14:45:00+03:00",
        "2026-11-18T16:02:00+03:00",
        1300,
    )
    # Все варианты успевают к 08:00 без лишнего ожидания, поэтому берётся самый дешёвый
    assert (back.mode, back.depart_at.isoformat(), back.price_rub) == ("shuttle", "2026-11-25T06:43:00+03:00", 1300)
    # С собакой шаттл не возьмут: едем на такси впритык к сроку
    assert (back_with_dog.mode, back_with_dog.depart_at.isoformat()) == ("taxi", "2026-11-25T07:12:00+03:00")


def test_hotel_outside_transfer_base_gives_no_leg() -> None:
    at = datetime(2026, 11, 18, 14, 0, tzinfo=ANTALYA)
    assert arrival_transfer_leg("osm:node/1", "AYT", at, Travelers()) is None
    assert departure_transfer_leg("osm:node/1", "AYT", at, Travelers()) is None


@pytest.mark.parametrize(
    ("start", "end", "target", "expected"),
    [
        ("06:00", "15:30", "22:31", "15:29"),
        ("06:00", "15:30", "10:00", "10:00"),
        ("22:00", "06:00", "12:00", "05:59"),  # окно через полночь
    ],
)
def test_latest_departure(start: str, end: str, target: str, expected: str) -> None:
    from app.booking.timeline import _latest_departure

    hours = Hours.model_validate({"from": start, "to": end})
    moment = datetime.combine(date(2026, 11, 24), time.fromisoformat(target), tzinfo=MALE)

    assert _latest_departure(hours, moment).strftime("%H:%M") == expected


def test_accepts_d1_contract_models() -> None:
    """Когда контракты D1 (#50) будут в main, их модели должны подходить без адаптеров."""
    tools = pytest.importorskip("app.contracts.tools")
    svo = tools.Place(
        kind="airport", iata="SVO", name="Шереметьево", latitude=55.97, longitude=37.41, timezone="Europe/Moscow"
    )
    dxb = tools.Place(kind="airport", iata="DXB", name="Dubai", latitude=25.25, longitude=55.37, timezone="Asia/Dubai")
    flight = tools.Flight(
        id="f1",
        origin=svo,
        destination=dxb,
        departure_at=datetime(2026, 11, 18, 23, 30, tzinfo=MSK),
        arrival_at=datetime(2026, 11, 19, 6, 0, tzinfo=DUBAI),
        price_rub=50000,
        is_synthetic=True,
        carrier="XX",
    )
    hotel = tools.Place(
        kind="hotel",
        id="osm:way/126601133",
        iata="DXB",
        name="Al Manzil",
        latitude=25.19,
        longitude=55.27,
        timezone="Asia/Dubai",
    )
    offer = tools.HotelOffer(
        hotel_id="osm:way/126601133",
        offer_id="o1",
        name="Al Manzil",
        place=hotel,
        check_in=date(2026, 11, 19),
        check_out=date(2026, 11, 25),
        price_rub=100000,
        check_in_time=time(14),
        check_out_time=time(12),
        is_synthetic=True,
    )

    timeline = build_timeline(flight, offer)

    assert timeline.event("check_in").at == datetime(2026, 11, 19, 14, 0, tzinfo=DUBAI)
