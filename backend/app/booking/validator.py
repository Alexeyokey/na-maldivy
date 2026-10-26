"""Валидатор стыковок (S5, #24): проблемы поездки и исправления с ценой.

Правила смотрят на таймлайн S3 (ожидания между событиями), на условия отеля, состав группы и трансферы S4.
Каждая проблема приходит с исправлениями. Цена исправления указывается, только если она известна
или честно оценена (price_estimated=True); иначе price_delta_rub=None — цену даст поиск, отель или пересборка.
Исправление не предлагается, если отель его точно не делает (ранний заезд недоступен).

patch описывает изменение для пересборки R4 (#23), поле kind:
  early_check_in, request_early_check_in, add_night_before, shift_check_in, request_late_check_out,
  add_night_after, shift_check_out, replace_transfer, replace_hotel, change_outbound, change_inbound,
  stopover_near_airport, notify_hotel_late_arrival.

Поля отеля берутся как у HotelOffer из D1 (#2) и могут отсутствовать: тогда правило считает их неизвестными.
Стыковки между рейсами не проверяются: в контрактах D1 у рейса нет сегментов.
"""

from collections.abc import Callable
from datetime import datetime, time, timedelta
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel

from app.booking.timeline import (
    AIRPORT_BEFORE_DEPARTURE_MIN,
    AIRPORT_EXIT_MIN,
    MODE_TITLES,
    StayLike,
    Timeline,
    TransferLeg,
)
from app.tools.transfers import (
    TransferBase,
    TransferOption,
    TransferQuote,
    Travelers,
    UnsupportedTransfer,
    quote_transfers,
)

Severity = Literal["critical", "warning", "info"]  # critical — без исправления поездка сорвётся
Rule = Literal[
    "airport_overnight_wait",
    "early_hotel_arrival",
    "wasted_first_night",
    "late_hotel_departure",
    "wasted_last_night",
    "long_airport_wait_before_departure",
    "missed_flight_risk",
    "transfer_before_arrival",
    "long_transfer",
    "night_arrival_with_infant",
    "adults_only_with_children",
    "pets_not_allowed",
    "transfer_pets",
    "transfer_child_seat",
    "transfer_prebook",
    "transfer_luggage",
    "reception_closed",
    "nonrefundable_rate",
    "visa_required",
]

AIRPORT_WAIT_WARNING_MIN = 120
AIRPORT_WAIT_CRITICAL_MIN = 240
HOTEL_WAIT_MIN = 180  # ждать заселения или трансфера после выезда дольше — уже проблема
LONG_TRANSFER_MIN = 90
MISSED_FLIGHT_MARGIN_MIN = 60  # приехать в аэропорт позже, чем за час до вылета, — не успеть
NIGHT_START, NIGHT_END = time(23), time(5)
INFANT_AGE = 3
CHILD_SEAT_CRITICAL_AGE = 4
RECEPTION_NIGHT_END = time(6)
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


class Fix(BaseModel):
    label: str
    price_delta_rub: int | None  # + доплата, − экономия, None — цену даст поиск, отель или пересборка
    price_estimated: bool = False
    patch: dict[str, Any]


class Issue(BaseModel):
    rule: Rule
    severity: Severity
    message: str
    at: AwareDatetime | None = None
    fixes: list[Fix] = []


class Requirements(BaseModel):
    """Что важно человеку. Временно здесь, потом придёт из брифа R1 (#4)."""

    refundable_required: bool = False
    dates_flexible: bool | None = None
    max_transfer_minutes: int | None = None  # None — порог LONG_TRANSFER_MIN без жёсткого требования
    visa_required: bool | None = None


def validate_trip(
    timeline: Timeline,
    stay: StayLike,
    travelers: Travelers,
    *,
    arrival_transfer: TransferLeg | None = None,
    departure_transfer: TransferLeg | None = None,
    requirements: Requirements | None = None,
    airport: str | None = None,
    base: TransferBase | None = None,
) -> list[Issue]:
    """Все проблемы поездки: сначала критичные, затем по времени. airport и base нужны, чтобы подобрать
    замену трансферу из базы S4; без них замена предлагается без конкретного варианта и цены."""
    ctx = _Context(timeline, stay, travelers, requirements or Requirements(), airport, base)
    issues: list[Issue] = []
    for rule in (
        _airport_overnight_wait,
        _early_hotel_arrival,
        _wasted_first_night,
        _late_hotel_departure,
        _wasted_last_night,
        _long_airport_wait_before_departure,
        _missed_flight_risk,
        _transfer_before_arrival,
        _night_arrival_with_infant,
        _hotel_rules,
        _reception_closed,
        _nonrefundable_rate,
        _visa_required,
    ):
        issues += rule(ctx, arrival_transfer, departure_transfer)
    if arrival_transfer:
        issues += _transfer_rules(ctx, arrival_transfer, "arrival")
    if departure_transfer:
        issues += _transfer_rules(ctx, departure_transfer, "departure", check_duration=arrival_transfer is None)
    return sorted(issues, key=lambda i: (SEVERITY_ORDER[i.severity], i.at is None, i.at.timestamp() if i.at else 0))


class _Context:
    def __init__(
        self,
        timeline: Timeline,
        stay: StayLike,
        travelers: Travelers,
        requirements: Requirements,
        airport: str | None,
        base: TransferBase | None,
    ) -> None:
        self.timeline, self.stay, self.travelers, self.req = timeline, stay, travelers, requirements
        self.airport, self.base = airport, base
        nights = (stay.check_out - stay.check_in).days
        price = getattr(stay, "price_rub", None)
        self.nightly: int | None = round(price / nights / 100) * 100 if price and nights > 0 else None

    def hotel(self, name: str) -> Any:
        return getattr(self.stay, name, None)

    def option(self, leg: TransferLeg) -> TransferOption | None:
        if not (self.airport and self.base):
            return None
        try:
            record = self.base.get(self.stay.hotel_id, self.airport)
        except UnsupportedTransfer:
            return None
        return next((o for o in record.options if o.id == leg.option_id), None)

    def night_fix(self, label: str, nights: int, patch: dict[str, Any]) -> Fix:
        price = self.nightly * nights if self.nightly is not None else None
        return Fix(label=label, price_delta_rub=price, price_estimated=price is not None, patch=patch)


def _airport_overnight_wait(ctx: _Context, arrival: TransferLeg | None, _: TransferLeg | None) -> list[Issue]:
    wait = ctx.timeline.wait("airport_after_arrival")
    if wait is None or wait.minutes < AIRPORT_WAIT_WARNING_MIN:
        return []
    overnight = _touches_night(wait.start, wait.end)
    severity: Severity = "critical" if overnight or wait.minutes >= AIRPORT_WAIT_CRITICAL_MIN else "warning"
    what = "Ночь в аэропорту" if overnight else "Долгое ожидание в аэропорту"
    mode = MODE_TITLES.get(arrival.mode, arrival.mode) if arrival else "трансфер"
    fixes = [
        Fix(
            label="Переночевать у аэропорта и уехать утром",
            price_delta_rub=None,
            patch={"kind": "stopover_near_airport", "airport": ctx.airport, "date": wait.start.date().isoformat()},
        ),
    ]
    option = ctx.option(arrival) if arrival else None
    change: dict[str, Any] = {"kind": "change_outbound"}
    label = "Выбрать рейс, который прилетает раньше"
    if option and option.hours:
        latest = datetime.combine(wait.start.date(), option.hours.end) - timedelta(minutes=AIRPORT_EXIT_MIN)
        change["arrive_before"] = latest.strftime("%H:%M")
        label = f"Выбрать рейс с прилётом до {latest:%H:%M}"
    fixes.append(Fix(label=label, price_delta_rub=None, patch=change))
    message = (
        f"{what}: {mode} только в {wait.end:%H:%M}, ждать {_duration(wait.minutes)} "
        f"({wait.start:%H:%M}–{wait.end:%H:%M})"
    )
    return [Issue(rule="airport_overnight_wait", severity=severity, at=wait.start, message=message, fixes=fixes)]


def _early_hotel_arrival(ctx: _Context, *_: TransferLeg | None) -> list[Issue]:
    wait = ctx.timeline.wait("hotel_before_check_in")
    if wait is None or wait.minutes < HOTEL_WAIT_MIN:
        return []
    fixes: list[Fix] = []
    available, price = ctx.hotel("early_check_in_available"), ctx.hotel("early_check_in_price_rub")
    if available is True:
        label = f"Ранний заезд за {_rub(price)}" if price is not None else "Ранний заезд, цену назовёт отель"
        fixes.append(Fix(label=label, price_delta_rub=price, patch={"kind": "early_check_in"}))
    elif available is None:
        fixes.append(
            Fix(label="Запросить ранний заезд у отеля", price_delta_rub=None, patch={"kind": "request_early_check_in"})
        )
    if ctx.timeline.booked_check_in.date() == wait.start.date():
        before = ctx.stay.check_in - timedelta(days=1)
        fixes.append(
            ctx.night_fix(
                f"Добавить ночь {before:%d.%m} и заселиться сразу",
                1,
                {"kind": "add_night_before", "check_in": before.isoformat()},
            )
        )
    fixes.append(
        Fix(
            label="Другой отель с ранним заездом",
            price_delta_rub=None,
            patch={"kind": "replace_hotel", "require": ["early_check_in"]},
        )
    )
    message = f"Приезд в отель в {wait.start:%H:%M}, заселение с {wait.end:%H:%M}: ждать {_duration(wait.minutes)}"
    return [Issue(rule="early_hotel_arrival", severity="warning", at=wait.start, message=message, fixes=fixes)]


def _wasted_first_night(ctx: _Context, *_: TransferLeg | None) -> list[Issue]:
    arrival, booked = ctx.timeline.hotel_arrival_at, ctx.timeline.booked_check_in
    late_days = (arrival.date() - booked.date()).days
    if late_days <= 0:
        return []
    bought_early_check_in = arrival.time() < ctx.stay.check_in_time
    wasted = late_days - int(bought_early_check_in)
    if wasted == 0:
        fix = ctx.night_fix(
            f"Заезд {arrival:%d.%m}: дешевле, но заселение с {ctx.stay.check_in_time:%H:%M}",
            -1,
            {"kind": "shift_check_in", "check_in": arrival.date().isoformat()},
        )
        message = f"Ночь {booked:%d.%m} оплачена, чтобы заселиться сразу по приезде в {arrival:%H:%M}"
        return [Issue(rule="wasted_first_night", severity="info", at=arrival, message=message, fixes=[fix])]
    new_check_in = booked.date() + timedelta(days=wasted)
    fix = ctx.night_fix(
        f"Сдвинуть заезд на {new_check_in:%d.%m}",
        -wasted,
        {"kind": "shift_check_in", "check_in": new_check_in.isoformat()},
    )
    message = (
        f"Оплачено ночей до приезда: {wasted}. Заезд по брони {booked:%d.%m}, "
        f"а приезд {arrival:%d.%m} в {arrival:%H:%M}"
    )
    return [Issue(rule="wasted_first_night", severity="warning", at=arrival, message=message, fixes=[fix])]


def _late_hotel_departure(ctx: _Context, *_: TransferLeg | None) -> list[Issue]:
    wait = ctx.timeline.wait("after_check_out")
    if wait is None or wait.minutes < HOTEL_WAIT_MIN:
        return []
    after = ctx.stay.check_out + timedelta(days=1)
    fixes = [
        Fix(label="Попросить поздний выезд", price_delta_rub=None, patch={"kind": "request_late_check_out"}),
        ctx.night_fix(
            f"Оставить номер ещё на ночь до {after:%d.%m}",
            1,
            {"kind": "add_night_after", "check_out": after.isoformat()},
        ),
    ]
    message = (
        f"Выезд из номера в {wait.start:%H:%M}, а в аэропорт только в {wait.end:%H:%M}: "
        f"{_duration(wait.minutes)} без номера"
    )
    return [Issue(rule="late_hotel_departure", severity="warning", at=wait.start, message=message, fixes=fixes)]


def _wasted_last_night(ctx: _Context, *_: TransferLeg | None) -> list[Issue]:
    leave = ctx.timeline.hotel_departure_at
    if leave is None:
        return []
    wasted = (ctx.timeline.booked_check_out.date() - leave.date()).days
    if wasted <= 0:
        return []
    fix = ctx.night_fix(
        f"Выезд {leave:%d.%m}", -wasted, {"kind": "shift_check_out", "check_out": leave.date().isoformat()}
    )
    message = (
        f"Оплачено ночей после отъезда: {wasted}. Уезжаете {leave:%d.%m} в {leave:%H:%M}, "
        f"а выезд по брони {ctx.timeline.booked_check_out:%d.%m}"
    )
    return [Issue(rule="wasted_last_night", severity="warning", at=leave, message=message, fixes=[fix])]


def _long_airport_wait_before_departure(
    ctx: _Context, _: TransferLeg | None, departure: TransferLeg | None
) -> list[Issue]:
    wait = ctx.timeline.wait("airport_before_departure")
    if wait is None or wait.minutes < HOTEL_WAIT_MIN:
        return []
    fixes = [
        Fix(
            label="Переночевать у аэропорта перед вылетом",
            price_delta_rub=None,
            patch={"kind": "stopover_near_airport", "airport": ctx.airport, "date": wait.start.date().isoformat()},
        )
    ]
    if departure:
        around = departure.arrive_at + timedelta(minutes=AIRPORT_BEFORE_DEPARTURE_MIN)
        fixes.insert(
            0,
            Fix(
                label=f"Выбрать обратный рейс около {around:%H:%M}",
                price_delta_rub=None,
                patch={"kind": "change_inbound", "depart_around": around.strftime("%H:%M")},
            ),
        )
    message = (
        f"В аэропорту с {wait.start:%H:%M}, а приезжать нужно только к {wait.end:%H:%M}: "
        f"лишние {_duration(wait.minutes)}"
    )
    return [
        Issue(
            rule="long_airport_wait_before_departure", severity="warning", at=wait.start, message=message, fixes=fixes
        )
    ]


def _missed_flight_risk(ctx: _Context, *_: TransferLeg | None) -> list[Issue]:
    arrive, flight = ctx.timeline.event("departure_transfer_arrival"), ctx.timeline.event("return_departure")
    if not (arrive and flight) or arrive.at <= flight.at - timedelta(minutes=MISSED_FLIGHT_MARGIN_MIN):
        return []
    deadline = flight.at - timedelta(minutes=AIRPORT_BEFORE_DEPARTURE_MIN)
    fix = Fix(
        label=f"Трансфер, который приезжает к {deadline:%H:%M}",
        price_delta_rub=None,
        patch={"kind": "replace_transfer", "leg": "departure", "arrive_before": deadline.isoformat()},
    )
    message = f"Не успеваете на рейс: трансфер в аэропорту в {arrive.at:%H:%M}, вылет в {flight.at:%H:%M}"
    return [Issue(rule="missed_flight_risk", severity="critical", at=arrive.at, message=message, fixes=[fix])]


def _transfer_before_arrival(ctx: _Context, *_: TransferLeg | None) -> list[Issue]:
    landed, transfer = ctx.timeline.event("outbound_arrival"), ctx.timeline.event("arrival_transfer_departure")
    if not (landed and transfer):
        return []
    ready = landed.at + timedelta(minutes=AIRPORT_EXIT_MIN)
    if transfer.at >= ready:
        return []
    fix = Fix(
        label=f"Трансфер не раньше {ready:%H:%M}",
        price_delta_rub=None,
        patch={"kind": "replace_transfer", "leg": "arrival", "depart_after": ready.isoformat()},
    )
    message = f"Трансфер в {transfer.at:%H:%M}, а выйти из аэропорта получится только к {ready:%H:%M}"
    return [Issue(rule="transfer_before_arrival", severity="critical", at=transfer.at, message=message, fixes=[fix])]


def _night_arrival_with_infant(ctx: _Context, *_: TransferLeg | None) -> list[Issue]:
    landed = ctx.timeline.event("outbound_arrival")
    infants = [age for age in ctx.travelers.children_ages if age < INFANT_AGE]
    if not (landed and infants) or not _is_night(landed.at.time()):
        return []
    fix = Fix(
        label="Выбрать рейс с прилётом днём",
        price_delta_rub=None,
        patch={
            "kind": "change_outbound",
            "arrive_after": NIGHT_END.strftime("%H:%M"),
            "arrive_before": NIGHT_START.strftime("%H:%M"),
        },
    )
    message = f"Ночной прилёт в {landed.at:%H:%M} с ребёнком до {INFANT_AGE} лет"
    return [Issue(rule="night_arrival_with_infant", severity="warning", at=landed.at, message=message, fixes=[fix])]


def _hotel_rules(ctx: _Context, *_: TransferLeg | None) -> list[Issue]:
    issues: list[Issue] = []
    name = ctx.stay.name
    if ctx.travelers.children_ages:
        adults_only = ctx.hotel("adults_only")
        if adults_only is True:
            message = f"{name} — отель только для взрослых, а в поездке ребёнок"
            issues.append(
                Issue(
                    rule="adults_only_with_children",
                    severity="critical",
                    message=message,
                    fixes=[_replace_hotel("children_allowed")],
                )
            )
        elif adults_only is None:
            message = f"Неизвестно, принимает ли {name} детей: уточнить до оплаты"
            issues.append(
                Issue(
                    rule="adults_only_with_children",
                    severity="warning",
                    message=message,
                    fixes=[_replace_hotel("children_allowed")],
                )
            )
    if ctx.travelers.pets:
        pets_allowed = ctx.hotel("pets_allowed")
        if pets_allowed is False:
            issues.append(
                Issue(
                    rule="pets_not_allowed",
                    severity="critical",
                    message=f"{name} не принимает животных",
                    fixes=[_replace_hotel("pets_allowed")],
                )
            )
        elif pets_allowed is None:
            message = f"Неизвестно, можно ли в {name} с животным: уточнить до оплаты"
            issues.append(
                Issue(
                    rule="pets_not_allowed", severity="warning", message=message, fixes=[_replace_hotel("pets_allowed")]
                )
            )
    return issues


def _reception_closed(ctx: _Context, *_: TransferLeg | None) -> list[Issue]:
    arrival = ctx.timeline.hotel_arrival_at
    night_arrival = arrival.time() < RECEPTION_NIGHT_END
    notify = Fix(
        label="Предупредить отель о позднем приезде",
        price_delta_rub=0,
        patch={"kind": "notify_hotel_late_arrival", "arrival": arrival.isoformat()},
    )
    always_open, closes = ctx.hotel("reception_24h"), ctx.hotel("reception_closes_at")
    if always_open is False and closes is not None and (arrival.time() >= closes or night_arrival):
        stopover = Fix(
            label="Переночевать у аэропорта",
            price_delta_rub=None,
            patch={"kind": "stopover_near_airport", "airport": ctx.airport, "date": arrival.date().isoformat()},
        )
        message = f"Ресепшен работает до {closes:%H:%M}, а приезд в {arrival:%H:%M}"
        return [
            Issue(rule="reception_closed", severity="critical", at=arrival, message=message, fixes=[notify, stopover])
        ]
    if always_open is None and (night_arrival or arrival.time() >= time(22)):
        message = f"Приезд в {arrival:%H:%M}: неизвестно, работает ли ресепшен ночью"
        return [Issue(rule="reception_closed", severity="info", at=arrival, message=message, fixes=[notify])]
    return []


def _nonrefundable_rate(ctx: _Context, *_: TransferLeg | None) -> list[Issue]:
    refundable, req = ctx.hotel("refundable"), ctx.req
    fix = _replace_hotel("refundable")
    if refundable is False:
        if req.refundable_required:
            message = f"Тариф {ctx.stay.name} невозвратный, а нужна возможность отмены"
            return [Issue(rule="nonrefundable_rate", severity="critical", message=message, fixes=[fix])]
        severity: Severity = "warning" if req.dates_flexible else "info"
        message = f"Тариф {ctx.stay.name} невозвратный: при переносе дат деньги не вернут"
        return [Issue(rule="nonrefundable_rate", severity=severity, message=message, fixes=[fix])]
    if refundable is None and req.refundable_required:
        message = f"Неизвестно, можно ли отменить бронь {ctx.stay.name}: уточнить до оплаты"
        return [Issue(rule="nonrefundable_rate", severity="warning", message=message, fixes=[fix])]
    return []


def _visa_required(ctx: _Context, *_: TransferLeg | None) -> list[Issue]:
    if not ctx.req.visa_required:
        return []
    return [Issue(rule="visa_required", severity="warning", message="Для поездки нужна виза: оформить заранее")]


def _transfer_rules(ctx: _Context, leg: TransferLeg, which: str, check_duration: bool = True) -> list[Issue]:
    issues: list[Issue] = []
    mode = MODE_TITLES.get(leg.mode, leg.mode).capitalize()
    where = "из аэропорта" if which == "arrival" else "в аэропорт"
    minutes = round((leg.arrive_at - leg.depart_at).total_seconds() / 60)
    limit = ctx.req.max_transfer_minutes or LONG_TRANSFER_MIN
    if check_duration and minutes > limit:
        strict = ctx.req.max_transfer_minutes is not None
        message = f"Трансфер {where} занимает {_duration(minutes)}"
        if strict:
            message += f", а нужно не дольше {_duration(limit)}"
        fix = Fix(
            label="Отель ближе к аэропорту",
            price_delta_rub=None,
            patch={"kind": "replace_hotel", "max_transfer_minutes": limit},
        )
        issues.append(
            Issue(
                rule="long_transfer",
                severity="critical" if strict else "warning",
                at=leg.depart_at,
                message=message,
                fixes=[fix],
            )
        )
    if "pets_forbidden" in leg.issues or "pets_unknown" in leg.issues:
        forbidden = "pets_forbidden" in leg.issues
        problem = "животных не берут" if forbidden else "неизвестно, возьмут ли животное"
        issues.append(
            Issue(
                rule="transfer_pets",
                severity="critical" if forbidden else "warning",
                at=leg.depart_at,
                message=f"{mode} {where}: {problem}",
                fixes=_transfer_alternatives(ctx, leg, which, _allows_pets),
            )
        )
    if "child_seat_missing" in leg.issues or "child_seat_unknown" in leg.issues:
        missing = "child_seat_missing" in leg.issues
        young = any(age < CHILD_SEAT_CRITICAL_AGE for age in ctx.travelers.children_ages)
        problem = "детского кресла нет" if missing else "неизвестно, будет ли детское кресло"
        issues.append(
            Issue(
                rule="transfer_child_seat",
                severity="critical" if missing and young else "warning",
                at=leg.depart_at,
                message=f"{mode} {where}: {problem}",
                fixes=_transfer_alternatives(ctx, leg, which, _has_child_seat),
            )
        )
    option = ctx.option(leg)
    if "prebook_required" in leg.issues:
        when = f"за {option.prebook_hours} ч" if option else "заранее"
        issues.append(
            Issue(
                rule="transfer_prebook",
                severity="info",
                at=leg.depart_at,
                message=f"{mode} {where} нужно бронировать {when}",
            )
        )
    if "luggage_limit" in leg.issues:
        detail = f": {option.luggage_limit}" if option and option.luggage_limit else ""
        issues.append(
            Issue(
                rule="transfer_luggage",
                severity="info",
                at=leg.depart_at,
                message=f"{mode} {where}: ограничение багажа{detail}",
            )
        )
    return issues


def _transfer_alternatives(
    ctx: _Context, leg: TransferLeg, which: str, suits: Callable[[TransferOption, TransferQuote], bool]
) -> list[Fix]:
    generic = Fix(label="Другой трансфер", price_delta_rub=None, patch={"kind": "replace_transfer", "leg": which})
    if not (ctx.airport and ctx.base):
        return [generic]
    try:
        quotes = quote_transfers(ctx.stay.hotel_id, ctx.airport, leg.depart_at, ctx.travelers, base=ctx.base)
    except UnsupportedTransfer:
        return [generic]
    options = {o.id: o for o in ctx.base.get(ctx.stay.hotel_id, ctx.airport).options}
    fixes = []
    for quote in quotes:
        if quote.option_id == leg.option_id or not quote.available or not suits(options[quote.option_id], quote):
            continue
        delta = quote.price_rub - leg.price_rub
        fixes.append(
            Fix(
                label=f"{MODE_TITLES.get(quote.mode, quote.mode).capitalize()}: {_signed_rub(delta)}",
                price_delta_rub=delta,
                patch={"kind": "replace_transfer", "leg": which, "option_id": quote.option_id},
            )
        )
    return fixes or [generic]


def _allows_pets(option: TransferOption, quote: TransferQuote) -> bool:
    return option.pets is True


def _has_child_seat(option: TransferOption, quote: TransferQuote) -> bool:
    return option.child_seat is True or option.pricing.type == "per_person"


def _replace_hotel(requirement: str) -> Fix:
    return Fix(label="Другой отель", price_delta_rub=None, patch={"kind": "replace_hotel", "require": [requirement]})


def _is_night(t: time) -> bool:
    return t >= NIGHT_START or t < NIGHT_END


def _touches_night(start: datetime, end: datetime) -> bool:
    return start.date() != end.date() or _is_night(start.time()) or _is_night(end.time())


def _duration(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} ч {rest} мин"
    return f"{hours} ч" if hours else f"{rest} мин"


def _rub(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₽"


def _signed_rub(amount: int) -> str:
    if amount == 0:
        return "без доплаты"
    return ("+" if amount > 0 else "−") + _rub(abs(amount))
