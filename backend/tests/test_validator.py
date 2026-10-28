from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.booking.timeline import TransferLeg, arrival_transfer_leg, build_timeline, departure_transfer_leg
from app.booking.validator import Issue, Requirements, validate_trip
from app.tools.transfers import TransferBase, Travelers, quote_transfers

MSK = ZoneInfo("Europe/Moscow")
DUBAI = ZoneInfo("Asia/Dubai")
MALE = ZoneInfo("Indian/Maldives")
ANTALYA = ZoneInfo("Europe/Istanbul")

CONRAD = "osm:node/4455235191"  # Мальдивы: только гидросамолёт 06:00–15:30, бронь за 72 ч, 20 кг багажа
KAYA_BELEK = "osm:node/897383953"  # Анталья: такси 3000, минивэн 4800 (кресло, животные), шаттл 650/чел
EFTALIA = "osm:node/8960913745"  # Аланья: шаттл 2 ч 53 мин


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
class Hotel:
    """Поля как у HotelOffer из D1; по умолчанию условия известны и не мешают."""

    hotel_id: str
    name: str
    place: Place
    check_in: date
    check_out: date
    price_rub: int | None = None
    check_in_time: time = time(14)
    check_out_time: time = time(12)
    adults_only: bool | None = False
    pets_allowed: bool | None = True
    refundable: bool | None = True
    reception_24h: bool | None = True
    reception_closes_at: time | None = None
    early_check_in_available: bool | None = None
    early_check_in_price_rub: int | None = None


SVO = Place("Шереметьево", "Europe/Moscow")
DXB = Place("Dubai International Airport", "Asia/Dubai")
MLE = Place("Velana International Airport", "Indian/Maldives")
AYT = Place("Antalya Airport", "Europe/Istanbul")
NIGHT_TO_DUBAI = Journey(
    "out", SVO, DXB, datetime(2026, 11, 18, 23, 30, tzinfo=MSK), datetime(2026, 11, 19, 6, 0, tzinfo=DUBAI)
)


@pytest.fixture(scope="module")
def base() -> TransferBase:
    return TransferBase.load()


def rules(issues: list[Issue]) -> list[tuple[str, str]]:
    return [(i.rule, i.severity) for i in issues]


def only(issues: list[Issue], rule: str) -> Issue:
    found = [i for i in issues if i.rule == rule]
    assert len(found) == 1, rules(issues)
    return found[0]


def fixes(issue: Issue) -> list[tuple[str, int | None]]:
    return [(f.label, f.price_delta_rub) for f in issue.fixes]


def leg_from_quote(hotel_id: str, airport: str, at: datetime, travelers: Travelers, mode: str) -> TransferLeg:
    quote = next(q for q in quote_transfers(hotel_id, airport, at, travelers) if q.mode == mode)
    return TransferLeg(
        option_id=quote.option_id,
        mode=quote.mode,
        depart_at=quote.depart_at,
        arrive_at=quote.arrive_at,
        price_rub=quote.price_rub,
        issues=quote.issues,
    )


def dubai_hotel(check_in: date, price: int, **rules_: object) -> Hotel:
    return Hotel(
        "osm:way/126601133",
        "Al Manzil",
        Place("Al Manzil", "Asia/Dubai"),
        check_in,
        date(2026, 11, 25),
        price_rub=price,
        **rules_,
    )


def test_maldives_night_arrival_is_critical_with_fixes(base: TransferBase) -> None:
    outbound = Journey(
        "out", SVO, MLE, datetime(2026, 11, 18, 14, 10, tzinfo=MSK), datetime(2026, 11, 18, 22, 55, tzinfo=MALE)
    )
    hotel = Hotel(
        CONRAD,
        "Conrad Rangali",
        Place("Conrad Rangali", "Indian/Maldives"),
        date(2026, 11, 19),
        date(2026, 11, 24),
        price_rub=330_000,
    )
    travelers = Travelers(adults=2)
    leg = arrival_transfer_leg(CONRAD, "MLE", outbound.arrival_at, travelers, base=base)

    issues = validate_trip(
        build_timeline(outbound, hotel, arrival_transfer=leg),
        hotel,
        travelers,
        arrival_transfer=leg,
        airport="MLE",
        base=base,
    )

    assert rules(issues) == [
        ("airport_overnight_wait", "critical"),
        ("early_hotel_arrival", "warning"),
        ("transfer_prebook", "info"),
        ("transfer_luggage", "info"),
    ]
    night = issues[0]
    assert night.message == "Ночь в аэропорту: гидросамолёт только в 06:00, ждать 6 ч 20 мин (23:40–06:00)"
    assert fixes(night) == [
        ("Переночевать у аэропорта и уехать утром", None),
        ("Выбрать рейс с прилётом до 14:45", None),
    ]
    assert night.fixes[0].patch == {"kind": "stopover_near_airport", "airport": "MLE", "date": "2026-11-18"}
    assert night.fixes[1].patch == {"kind": "change_outbound", "arrive_before": "14:45"}
    early = issues[1]
    assert early.message == "Приезд в отель в 07:29, заселение с 14:00: ждать 6 ч 31 мин"
    # Ранний заезд неизвестен — только запрос; ночь накануне по цене ночи этого отеля (330 000 / 5)
    assert fixes(early) == [
        ("Запросить ранний заезд у отеля", None),
        ("Добавить ночь 18.11 и заселиться сразу", 66_000),
        ("Другой отель с ранним заездом", None),
    ]
    assert early.fixes[1].price_estimated
    assert issues[2].message == "Гидросамолёт из аэропорта нужно бронировать за 72 ч"
    assert (
        issues[3].message
        == "Гидросамолёт из аэропорта: ограничение багажа: 20 кг багажа и 5 кг ручной клади на человека"
    )


def test_dubai_night_flight_offers_paid_early_check_in() -> None:
    hotel = dubai_hotel(date(2026, 11, 19), 300_000, early_check_in_available=True, early_check_in_price_rub=3000)

    issues = validate_trip(build_timeline(NIGHT_TO_DUBAI, hotel), hotel, Travelers(adults=2))

    assert rules(issues) == [("early_hotel_arrival", "warning")]
    assert fixes(issues[0]) == [
        ("Ранний заезд за 3 000 ₽", 3000),
        ("Добавить ночь 18.11 и заселиться сразу", 50_000),
        ("Другой отель с ранним заездом", None),
    ]
    assert not issues[0].fixes[0].price_estimated
    assert issues[0].fixes[1].patch == {"kind": "add_night_before", "check_in": "2026-11-18"}


def test_night_bought_for_early_check_in_is_only_info() -> None:
    hotel = dubai_hotel(date(2026, 11, 18), 350_000)

    issues = validate_trip(build_timeline(NIGHT_TO_DUBAI, hotel), hotel, Travelers(adults=2))

    assert rules(issues) == [("wasted_first_night", "info")]
    assert issues[0].message == "Ночь 18.11 оплачена, чтобы заселиться сразу по приезде в 06:45"
    assert fixes(issues[0]) == [("Заезд 19.11: дешевле, но заселение с 14:00", -50_000)]


def test_first_night_wasted_when_arriving_in_the_afternoon() -> None:
    day_flight = Journey(
        "out", SVO, DXB, datetime(2026, 11, 19, 9, 0, tzinfo=MSK), datetime(2026, 11, 19, 15, 0, tzinfo=DUBAI)
    )
    hotel = dubai_hotel(date(2026, 11, 18), 350_000)

    issues = validate_trip(build_timeline(day_flight, hotel), hotel, Travelers(adults=2))

    issue = only(issues, "wasted_first_night")
    assert issue.severity == "warning"
    assert issue.message == "Оплачено ночей до приезда: 1. Заезд по брони 18.11, а приезд 19.11 в 15:45"
    assert fixes(issue) == [("Сдвинуть заезд на 19.11", -50_000)]
    assert issue.fixes[0].patch == {"kind": "shift_check_in", "check_in": "2026-11-19"}


def test_late_flight_home_leaves_hours_without_room() -> None:
    inbound = Journey(
        "back", DXB, SVO, datetime(2026, 11, 25, 23, 50, tzinfo=DUBAI), datetime(2026, 11, 26, 3, 50, tzinfo=MSK)
    )
    hotel = dubai_hotel(date(2026, 11, 19), 300_000)

    issues = validate_trip(build_timeline(NIGHT_TO_DUBAI, hotel, inbound), hotel, Travelers(adults=2))

    issue = only(issues, "late_hotel_departure")
    assert issue.message == "Выезд из номера в 12:00, а в аэропорт только в 21:50: 9 ч 50 мин без номера"
    assert fixes(issue) == [("Попросить поздний выезд", None), ("Оставить номер ещё на ночь до 26.11", 50_000)]


def test_maldives_flight_home_at_night(base: TransferBase) -> None:
    outbound = Journey(
        "out", SVO, MLE, datetime(2026, 11, 18, 2, 0, tzinfo=MSK), datetime(2026, 11, 18, 10, 45, tzinfo=MALE)
    )
    inbound = Journey(
        "back", MLE, SVO, datetime(2026, 11, 25, 2, 0, tzinfo=MALE), datetime(2026, 11, 25, 8, 30, tzinfo=MSK)
    )
    hotel = Hotel(
        CONRAD,
        "Conrad Rangali",
        Place("Conrad Rangali", "Indian/Maldives"),
        date(2026, 11, 18),
        date(2026, 11, 25),
        price_rub=462_000,
    )
    travelers = Travelers(adults=2)
    leg = departure_transfer_leg(CONRAD, "MLE", inbound.departure_at, travelers, base=base)

    issues = validate_trip(
        build_timeline(outbound, hotel, inbound, departure_transfer=leg),
        hotel,
        travelers,
        departure_transfer=leg,
        airport="MLE",
        base=base,
    )

    assert rules(issues) == [
        ("wasted_last_night", "warning"),
        ("long_airport_wait_before_departure", "warning"),
        ("transfer_prebook", "info"),
        ("transfer_luggage", "info"),
    ]
    wasted, wait = issues[0], issues[1]
    assert wasted.message == "Оплачено ночей после отъезда: 1. Уезжаете 24.11 в 15:29, а выезд по брони 25.11"
    assert fixes(wasted) == [("Выезд 24.11", -66_000)]
    assert wait.message == "В аэропорту с 16:58, а приезжать нужно только к 00:00: лишние 7 ч 2 мин"
    assert fixes(wait) == [
        ("Выбрать обратный рейс около 18:58", None),
        ("Переночевать у аэропорта перед вылетом", None),
    ]


def test_missed_flight_and_transfer_before_landing_are_critical() -> None:
    inbound = Journey(
        "back", DXB, SVO, datetime(2026, 11, 25, 23, 50, tzinfo=DUBAI), datetime(2026, 11, 26, 3, 50, tzinfo=MSK)
    )
    hotel = dubai_hotel(date(2026, 11, 19), 300_000)
    too_early = TransferLeg(
        option_id="a",
        mode="taxi",
        depart_at=datetime(2026, 11, 19, 6, 10, tzinfo=DUBAI),
        arrive_at=datetime(2026, 11, 19, 7, 0, tzinfo=DUBAI),
        price_rub=1750,
    )
    too_late = TransferLeg(
        option_id="d",
        mode="taxi",
        depart_at=datetime(2026, 11, 25, 23, 0, tzinfo=DUBAI),
        arrive_at=datetime(2026, 11, 25, 23, 40, tzinfo=DUBAI),
        price_rub=1750,
    )

    timeline = build_timeline(NIGHT_TO_DUBAI, hotel, inbound, arrival_transfer=too_early, departure_transfer=too_late)
    issues = validate_trip(
        timeline, hotel, Travelers(adults=2), arrival_transfer=too_early, departure_transfer=too_late
    )

    assert rules(issues)[:2] == [("transfer_before_arrival", "critical"), ("missed_flight_risk", "critical")]
    assert issues[0].message == "Трансфер в 06:10, а выйти из аэропорта получится только к 06:45"
    assert issues[1].message == "Не успеваете на рейс: трансфер в аэропорту в 23:40, вылет в 23:50"
    assert fixes(issues[1]) == [("Трансфер, который приезжает к 21:50", None)]


def test_long_transfer_warning_or_critical_by_requirement(base: TransferBase) -> None:
    outbound = Journey(
        "out", SVO, AYT, datetime(2026, 11, 18, 9, 0, tzinfo=MSK), datetime(2026, 11, 18, 12, 15, tzinfo=ANTALYA)
    )
    hotel = Hotel(
        EFTALIA, "Eftalia Aytur", Place("Eftalia Aytur", "Europe/Istanbul"), date(2026, 11, 18), date(2026, 11, 25)
    )
    travelers = Travelers(adults=2, children_ages=[4])
    leg = arrival_transfer_leg(EFTALIA, "AYT", outbound.arrival_at, travelers, base=base)
    timeline = build_timeline(outbound, hotel, arrival_transfer=leg)

    soft = only(validate_trip(timeline, hotel, travelers, arrival_transfer=leg), "long_transfer")
    strict = only(
        validate_trip(
            timeline, hotel, travelers, arrival_transfer=leg, requirements=Requirements(max_transfer_minutes=60)
        ),
        "long_transfer",
    )

    assert (soft.severity, soft.message) == ("warning", "Трансфер из аэропорта занимает 2 ч 53 мин")
    assert (strict.severity, strict.message) == (
        "critical",
        "Трансфер из аэропорта занимает 2 ч 53 мин, а нужно не дольше 1 ч",
    )
    assert strict.fixes[0].patch == {"kind": "replace_hotel", "max_transfer_minutes": 60}


def test_long_return_transfer_is_checked_even_when_arrival_transfer_is_short() -> None:
    hotel = dubai_hotel(date(2026, 11, 19), 350_000)
    inbound = Journey("back", DXB, SVO, datetime(2026, 11, 25, 20, tzinfo=DUBAI), datetime(2026, 11, 26, 1, tzinfo=MSK))
    arrival = TransferLeg(
        option_id="short-arrival",
        mode="taxi",
        price_rub=1000,
        depart_at=datetime(2026, 11, 19, 6, 45, tzinfo=DUBAI),
        arrive_at=datetime(2026, 11, 19, 7, 15, tzinfo=DUBAI),
    )
    departure = TransferLeg(
        option_id="long-return",
        mode="shuttle",
        price_rub=1000,
        depart_at=datetime(2026, 11, 25, 15, tzinfo=DUBAI),
        arrive_at=datetime(2026, 11, 25, 18, tzinfo=DUBAI),
    )
    timeline = build_timeline(NIGHT_TO_DUBAI, hotel, inbound, arrival, departure)
    issue = only(
        validate_trip(
            timeline,
            hotel,
            Travelers(adults=2),
            arrival_transfer=arrival,
            departure_transfer=departure,
            requirements=Requirements(max_transfer_minutes=60),
        ),
        "long_transfer",
    )
    assert issue.severity == "critical"
    assert issue.message == "Трансфер в аэропорт занимает 3 ч, а нужно не дольше 1 ч"


def test_known_zero_hotel_price_is_not_changed_into_unknown_night_fix_price() -> None:
    hotel = dubai_hotel(date(2026, 11, 19), 0)
    issue = only(
        validate_trip(build_timeline(NIGHT_TO_DUBAI, hotel), hotel, Travelers(adults=2)), "early_hotel_arrival"
    )
    extra_night = next(fix for fix in issue.fixes if fix.patch["kind"] == "add_night_before")
    assert extra_night.price_delta_rub == 0
    assert extra_night.price_estimated is True


@pytest.mark.parametrize("max_transfer_minutes", [None, 40])
def test_child_seat_replacement_respects_pet_and_duration_constraints(
    base: TransferBase,
    max_transfer_minutes: int | None,
) -> None:
    hotel = Hotel(
        KAYA_BELEK, "Kaya Belek", Place("Kaya Belek", "Europe/Istanbul"), date(2026, 11, 18), date(2026, 11, 25)
    )
    flight = Journey(
        "out", SVO, AYT, datetime(2026, 11, 18, 11, tzinfo=MSK), datetime(2026, 11, 18, 14, tzinfo=ANTALYA)
    )
    travelers = Travelers(adults=2, children_ages=[3], pets=1)
    taxi = leg_from_quote(KAYA_BELEK, "AYT", datetime(2026, 11, 18, 14, 45, tzinfo=ANTALYA), travelers, "taxi")
    issue = only(
        validate_trip(
            build_timeline(flight, hotel, arrival_transfer=taxi),
            hotel,
            travelers,
            arrival_transfer=taxi,
            requirements=Requirements(max_transfer_minutes=max_transfer_minutes),
            airport="AYT",
            base=base,
        ),
        "transfer_child_seat",
    )
    expected = ["private_minivan"] if max_transfer_minutes is None else [""]
    assert [fix.patch.get("option_id", "").rsplit(":", 1)[-1] for fix in issue.fixes] == expected


@pytest.mark.parametrize("replacement_minutes", [30, 180])
def test_return_transfer_replacement_must_still_reach_airport_before_flight(
    base: TransferBase,
    replacement_minutes: int,
) -> None:
    original = base.get(KAYA_BELEK, "AYT")
    options = [
        option.model_copy(update={"duration_min": replacement_minutes}) if option.mode == "private_minivan" else option
        for option in original.options
        if option.mode in {"taxi", "private_minivan"}
    ]
    checked_base = TransferBase([original.model_copy(update={"options": options})])
    hotel = Hotel(
        KAYA_BELEK, "Kaya Belek", Place("Kaya Belek", "Europe/Istanbul"), date(2026, 11, 18), date(2026, 11, 25)
    )
    flight = Journey(
        "out", SVO, AYT, datetime(2026, 11, 18, 11, tzinfo=MSK), datetime(2026, 11, 18, 14, tzinfo=ANTALYA)
    )
    inbound = Journey(
        "back", AYT, SVO, datetime(2026, 11, 25, 10, tzinfo=ANTALYA), datetime(2026, 11, 25, 13, tzinfo=MSK)
    )
    travelers = Travelers(adults=2, pets=1)
    quote = next(
        q
        for q in quote_transfers(
            KAYA_BELEK, "AYT", datetime(2026, 11, 25, 7, tzinfo=ANTALYA), travelers, base=checked_base
        )
        if q.mode == "taxi"
    )
    departure = TransferLeg(
        option_id=quote.option_id,
        mode=quote.mode,
        depart_at=quote.depart_at,
        arrive_at=quote.arrive_at,
        price_rub=quote.price_rub,
        issues=quote.issues,
    )
    issue = only(
        validate_trip(
            build_timeline(flight, hotel, inbound, departure_transfer=departure),
            hotel,
            travelers,
            departure_transfer=departure,
            airport="AYT",
            base=checked_base,
        ),
        "transfer_pets",
    )
    assert any(fix.patch.get("option_id", "").endswith(":private_minivan") for fix in issue.fixes) is (
        replacement_minutes == 30
    )


def test_night_arrival_with_infant() -> None:
    late = Journey(
        "out", SVO, DXB, datetime(2026, 11, 18, 20, 0, tzinfo=MSK), datetime(2026, 11, 19, 2, 0, tzinfo=DUBAI)
    )
    hotel = dubai_hotel(date(2026, 11, 18), 350_000)

    with_baby = validate_trip(build_timeline(late, hotel), hotel, Travelers(adults=2, children_ages=[1]))
    with_teen = validate_trip(build_timeline(late, hotel), hotel, Travelers(adults=2, children_ages=[12]))

    assert only(with_baby, "night_arrival_with_infant").message == "Ночной прилёт в 02:00 с ребёнком до 3 лет"
    assert "night_arrival_with_infant" not in [i.rule for i in with_teen]


class TestTrapsFromD3:
    """Пять ловушек data/traps.md (ветка У2): ошибочный вариант подаётся напрямую, минуя фильтры поиска."""

    def hotel(self, hotel_id: str, **rules_: object) -> Hotel:
        return Hotel(
            hotel_id,
            "Trap Hotel",
            Place("Trap Hotel", "Europe/Istanbul"),
            date(2026, 11, 18),
            date(2026, 11, 25),
            price_rub=37_170,
            **rules_,
        )

    def day_flight(self) -> Journey:
        return Journey(
            "out", SVO, AYT, datetime(2026, 11, 18, 11, 0, tzinfo=MSK), datetime(2026, 11, 18, 14, 0, tzinfo=ANTALYA)
        )

    def test_1_nonrefundable(self) -> None:
        hotel = self.hotel("osm:node/530098775", refundable=False)
        timeline = build_timeline(self.day_flight(), hotel)

        def severity(req: Requirements) -> str:
            return only(
                validate_trip(timeline, hotel, Travelers(adults=2), requirements=req), "nonrefundable_rate"
            ).severity

        assert severity(Requirements(refundable_required=True)) == "critical"
        assert severity(Requirements(dates_flexible=True)) == "warning"
        assert severity(Requirements()) == "info"

    def test_2_adults_only_with_child(self) -> None:
        travelers = Travelers(adults=2, children_ages=[5])
        known = self.hotel("osm:node/3623376904", adults_only=True)
        unknown = self.hotel("osm:node/3623376904", adults_only=None)

        issue = only(
            validate_trip(build_timeline(self.day_flight(), known), known, travelers), "adults_only_with_children"
        )
        maybe = only(
            validate_trip(build_timeline(self.day_flight(), unknown), unknown, travelers), "adults_only_with_children"
        )

        assert (issue.severity, maybe.severity) == ("critical", "warning")
        assert issue.fixes[0].patch == {"kind": "replace_hotel", "require": ["children_allowed"]}

    def test_3_early_arrival_without_early_check_in(self) -> None:
        dawn = Journey(
            "out", SVO, AYT, datetime(2026, 11, 18, 2, 15, tzinfo=MSK), datetime(2026, 11, 18, 5, 15, tzinfo=ANTALYA)
        )
        hotel = self.hotel("osm:node/10219505800", early_check_in_available=False)

        issue = only(validate_trip(build_timeline(dawn, hotel), hotel, Travelers(adults=2)), "early_hotel_arrival")

        # Восьмичасовой разрыв; раннего заезда нет — его не предлагаем, только ночь накануне или другой отель
        assert issue.message == "Приезд в отель в 06:00, заселение с 14:00: ждать 8 ч"
        assert fixes(issue) == [
            ("Добавить ночь 17.11 и заселиться сразу", 5300),
            ("Другой отель с ранним заездом", None),
        ]

    def test_4_two_hour_transfer(self) -> None:
        hotel = self.hotel("osm:node/11725204537")
        flight = self.day_flight()
        leg = TransferLeg(
            option_id="synthetic",
            mode="taxi",
            depart_at=datetime(2026, 11, 18, 14, 45, tzinfo=ANTALYA),
            arrive_at=datetime(2026, 11, 18, 16, 45, tzinfo=ANTALYA),
            price_rub=6000,
        )

        issues = validate_trip(
            build_timeline(flight, hotel, arrival_transfer=leg),
            hotel,
            Travelers(adults=2),
            arrival_transfer=leg,
            requirements=Requirements(max_transfer_minutes=60),
        )

        assert rules(issues) == [("long_transfer", "critical")]

    def test_5_pets_ban(self) -> None:
        hotel = self.hotel("osm:node/1280250536", pets_allowed=False)

        issue = only(
            validate_trip(build_timeline(self.day_flight(), hotel), hotel, Travelers(adults=2, pets=1)),
            "pets_not_allowed",
        )

        assert issue.severity == "critical"
        assert issue.message == "Trap Hotel не принимает животных"


def test_transfer_with_dog_offers_minivan(base: TransferBase) -> None:
    flight = Journey(
        "out", SVO, AYT, datetime(2026, 11, 18, 11, 0, tzinfo=MSK), datetime(2026, 11, 18, 14, 0, tzinfo=ANTALYA)
    )
    hotel = Hotel(
        KAYA_BELEK, "Kaya Belek", Place("Kaya Belek", "Europe/Istanbul"), date(2026, 11, 18), date(2026, 11, 25)
    )
    travelers = Travelers(adults=2, pets=1)
    pickup = datetime(2026, 11, 18, 14, 45, tzinfo=ANTALYA)

    taxi = arrival_transfer_leg(KAYA_BELEK, "AYT", flight.arrival_at, travelers, base=base)
    shuttle = leg_from_quote(KAYA_BELEK, "AYT", pickup, travelers, "shuttle")
    unknown = only(
        validate_trip(
            build_timeline(flight, hotel, arrival_transfer=taxi),
            hotel,
            travelers,
            arrival_transfer=taxi,
            airport="AYT",
            base=base,
        ),
        "transfer_pets",
    )
    forbidden = only(
        validate_trip(
            build_timeline(flight, hotel, arrival_transfer=shuttle),
            hotel,
            travelers,
            arrival_transfer=shuttle,
            airport="AYT",
            base=base,
        ),
        "transfer_pets",
    )

    assert taxi.mode == "taxi"  # шаттл с собакой не берут, поэтому первым идёт такси
    assert (unknown.severity, unknown.message) == ("warning", "Такси из аэропорта: неизвестно, возьмут ли животное")
    assert fixes(unknown) == [("Минивэн: +1 800 ₽", 1800)]
    assert unknown.fixes[0].patch == {
        "kind": "replace_transfer",
        "leg": "arrival",
        "option_id": "osm:node/897383953:AYT:private_minivan",
    }
    assert (forbidden.severity, fixes(forbidden)) == ("critical", [("Минивэн: +3 500 ₽", 3500)])


def test_taxi_without_known_child_seat(base: TransferBase) -> None:
    flight = Journey(
        "out", SVO, AYT, datetime(2026, 11, 18, 11, 0, tzinfo=MSK), datetime(2026, 11, 18, 14, 0, tzinfo=ANTALYA)
    )
    hotel = Hotel(
        KAYA_BELEK, "Kaya Belek", Place("Kaya Belek", "Europe/Istanbul"), date(2026, 11, 18), date(2026, 11, 25)
    )
    travelers = Travelers(adults=2, children_ages=[3])
    taxi = leg_from_quote(KAYA_BELEK, "AYT", datetime(2026, 11, 18, 14, 45, tzinfo=ANTALYA), travelers, "taxi")

    with_base = only(
        validate_trip(
            build_timeline(flight, hotel, arrival_transfer=taxi),
            hotel,
            travelers,
            arrival_transfer=taxi,
            airport="AYT",
            base=base,
        ),
        "transfer_child_seat",
    )
    without_base = only(
        validate_trip(build_timeline(flight, hotel, arrival_transfer=taxi), hotel, travelers, arrival_transfer=taxi),
        "transfer_child_seat",
    )

    assert (with_base.severity, with_base.message) == (
        "warning",
        "Такси из аэропорта: неизвестно, будет ли детское кресло",
    )
    # Шаттл с оплатой за место — кресло не нужно; минивэн с креслом
    assert fixes(with_base) == [("Шаттл: −1 400 ₽", -1400), ("Минивэн: +1 800 ₽", 1800)]
    assert fixes(without_base) == [("Другой трансфер", None)]


def test_reception_closed_on_late_arrival() -> None:
    late = Journey(
        "out", SVO, DXB, datetime(2026, 11, 18, 16, 45, tzinfo=MSK), datetime(2026, 11, 18, 22, 45, tzinfo=DUBAI)
    )
    closing = dubai_hotel(date(2026, 11, 18), 350_000, reception_24h=False, reception_closes_at=time(22))
    unknown = dubai_hotel(date(2026, 11, 18), 350_000, reception_24h=None)

    closed = only(validate_trip(build_timeline(late, closing), closing, Travelers(adults=2)), "reception_closed")
    maybe = only(validate_trip(build_timeline(late, unknown), unknown, Travelers(adults=2)), "reception_closed")

    assert (closed.severity, closed.message) == ("critical", "Ресепшен работает до 22:00, а приезд в 23:30")
    assert fixes(closed) == [("Предупредить отель о позднем приезде", 0), ("Переночевать у аэропорта", None)]
    assert (maybe.severity, maybe.message) == ("info", "Приезд в 23:30: неизвестно, работает ли ресепшен ночью")


def test_visa_required() -> None:
    hotel = dubai_hotel(date(2026, 11, 19), 300_000)
    issues = validate_trip(
        build_timeline(NIGHT_TO_DUBAI, hotel), hotel, Travelers(), requirements=Requirements(visa_required=True)
    )
    assert only(issues, "visa_required").severity == "warning"


def test_clean_trip_has_no_issues(base: TransferBase) -> None:
    outbound = Journey(
        "out", SVO, AYT, datetime(2026, 11, 18, 11, 0, tzinfo=MSK), datetime(2026, 11, 18, 14, 0, tzinfo=ANTALYA)
    )
    inbound = Journey(
        "back", AYT, SVO, datetime(2026, 11, 25, 10, 0, tzinfo=ANTALYA), datetime(2026, 11, 25, 13, 30, tzinfo=MSK)
    )
    hotel = Hotel(
        KAYA_BELEK,
        "Kaya Belek",
        Place("Kaya Belek", "Europe/Istanbul"),
        date(2026, 11, 18),
        date(2026, 11, 25),
        price_rub=70_000,
    )
    travelers = Travelers(adults=2)
    there = arrival_transfer_leg(KAYA_BELEK, "AYT", outbound.arrival_at, travelers, base=base)
    back = departure_transfer_leg(KAYA_BELEK, "AYT", inbound.departure_at, travelers, base=base)

    timeline = build_timeline(outbound, hotel, inbound, arrival_transfer=there, departure_transfer=back)
    issues = validate_trip(
        timeline, hotel, travelers, arrival_transfer=there, departure_transfer=back, airport="AYT", base=base
    )

    assert issues == []


def test_stay_without_hotel_rules_counts_as_unknown() -> None:
    """Объект без полей правил (как в тестах S3) не ломает валидатор: правила считаются неизвестными."""

    @dataclass
    class BareStay:
        hotel_id: str
        name: str
        place: Place
        check_in: date
        check_out: date
        check_in_time: time = time(14)
        check_out_time: time = time(12)

    stay = BareStay("h", "Bare", Place("Bare", "Asia/Dubai"), date(2026, 11, 19), date(2026, 11, 25))

    issues = validate_trip(build_timeline(NIGHT_TO_DUBAI, stay), stay, Travelers(adults=2, children_ages=[6], pets=1))

    assert ("adults_only_with_children", "warning") in rules(issues)
    assert ("pets_not_allowed", "warning") in rules(issues)
    early = only(issues, "early_hotel_arrival")
    assert ("Добавить ночь 18.11 и заселиться сразу", None) in fixes(early)  # цены нет — не выдумываем


def test_accepts_d1_hotel_offer() -> None:
    """Когда контракты D1 (#50) будут в main, правила отеля должны читать поля HotelOffer напрямую."""
    tools = pytest.importorskip("app.contracts.tools")
    place = tools.Place(
        kind="hotel",
        id="osm:node/3623376904",
        iata="AYT",
        name="Trap",
        latitude=36.88,
        longitude=30.7,
        timezone="Europe/Istanbul",
    )
    offer = tools.HotelOffer(
        hotel_id="osm:node/3623376904",
        offer_id="o",
        name="Trap",
        place=place,
        check_in=date(2026, 11, 18),
        check_out=date(2026, 11, 25),
        price_rub=32_760,
        adults_only=True,
        refundable=False,
        is_synthetic=True,
    )
    flight = Journey(
        "out", SVO, AYT, datetime(2026, 11, 18, 11, 0, tzinfo=MSK), datetime(2026, 11, 18, 14, 0, tzinfo=ANTALYA)
    )

    issues = validate_trip(
        build_timeline(flight, offer),
        offer,
        Travelers(adults=2, children_ages=[5]),
        requirements=Requirements(refundable_required=True),
    )

    assert ("adults_only_with_children", "critical") in rules(issues)
    assert ("nonrefundable_rate", "critical") in rules(issues)
