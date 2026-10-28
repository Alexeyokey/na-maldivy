"""Действия агента с инструментами D1: подбор направлений и сборка поездки.

Аргументы инструментов собирает код из брифа, а не модель: так модель физически не может
поменять бюджет, даты или состав группы. Модель потом объясняет результат по фактам отсюда.

Сборка поездки перебирает сочетания «рейс туда × отель × рейс обратно», для каждого берёт
трансфер, строит таймлайн S3 и прогоняет валидатор стыковок S5. Оценка сочетаний здесь
временная и простая; полноценное ранжирование с весами и Парето делает R2 (#12).
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, timedelta
from typing import Any

from app.agent.brief import TripBrief
from app.agent.hidden import NEEDS
from app.agent.places import place_name
from app.booking.timeline import AIRPORT_EXIT_MIN, build_timeline
from app.booking.validator import Issue, Requirements, validate_trip
from app.contracts.tools import Destination, Flight, HotelOffer, Train, Transfer
from app.tools.s4_adapter import transfer_leg

log = logging.getLogger(__name__)

# Вызов инструмента с событиями и обработкой ошибок; None — инструмент не ответил.
ToolCaller = Callable[[str, dict[str, Any]], Awaitable[Any | None]]

DEFAULT_NIGHTS = 7
SHORT_FLIGHT_MIN = 5 * 60
WARM_AIR_C = 24
# Штрафы оценки в рублях: «критичная проблема стыковки стоит как 30 тысяч переплаты».
PENALTY = {"critical": 30_000, "warning": 5_000, "info": 0}


@dataclass
class Assumption:
    field: str
    text: str


@dataclass
class PlanResult:
    options: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # что не нашлось и почему
    start_date: date | None = None


def _month_key(month: int, today: date) -> str:
    year = today.year if month >= today.month else today.year + 1
    return f"{year}-{month:02d}"


def rub(amount: int | None) -> str:
    return "цена неизвестна" if amount is None else f"{amount:,}".replace(",", " ") + " ₽"


def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(5, "ночь", "ночи", "ночей") → «5 ночей»."""
    form = one if n % 10 == 1 and n % 100 != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many
    return f"{n} {form}"


def hours(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    if not h:
        return f"{m} мин"
    return f"{h} ч {m:02d} мин" if m else f"{h} ч"


# ---------------------------------------------------------------------------
# Вдохновение: куда поехать
# ---------------------------------------------------------------------------


async def suggest(brief: TripBrief, call: ToolCaller) -> PlanResult:
    result = PlanResult()
    assert brief.origin and brief.month
    nights = brief.nights or DEFAULT_NIGHTS
    if brief.nights is None:
        result.assumptions.append(Assumption("nights", f"считаю на {plural(nights, 'ночь', 'ночи', 'ночей')}"))
    args: dict[str, Any] = {
        "month": brief.month,
        "origin": brief.origin,
        "travelers": brief.travelers.model_dump(),
        "nights": nights,
        "budget_rub": brief.budget_rub,
        "min_water_temperature_c": brief.min_water_temperature_c,
        "visa_free_only": brief.visa_free_only,
    }
    found: list[Destination] | None = await call("suggest_destinations", args)
    relaxed_budget = False
    if found == [] and brief.budget_rub is not None:
        found = await call("suggest_destinations", {**args, "budget_rub": None})
        relaxed_budget = True
    if found is None:
        result.notes.append("Источник направлений недоступен; условия поездки не подтверждены")
        return result
    options = []
    for d in found:
        air, water, flight, price = (
            d.air_temperature_c,
            d.water_temperature_c,
            d.flight_duration_minutes,
            d.estimated_total_price_rub,
        )
        # Hard requirements cannot be satisfied by an unknown value.
        if brief.min_water_temperature_c is not None and (water is None or water < brief.min_water_temperature_c):
            continue
        if brief.visa_free_only and d.visa_required is not False:
            continue
        if brief.max_flight_minutes is not None and (flight is None or flight > brief.max_flight_minutes):
            continue
        why, tradeoffs, risks = [], [], list(d.risks)
        if water is None:
            risks.append("Температура воды неизвестна")
        elif water >= 24 and {"sea", "warm"} & set(brief.needs):
            why.append(f"вода {water:+g} °C")
        if air is None:
            risks.append("Температура воздуха неизвестна")
        elif "warm" in brief.needs and air < WARM_AIR_C:
            tradeoffs.append(f"днём только {air:+g} °C")
        if flight is None:
            risks.append("Время перелёта неизвестно")
        elif flight <= SHORT_FLIGHT_MIN:
            why.append(f"оценка перелёта {hours(flight)}")
        elif "short_flight" in brief.needs or "child" in brief.needs:
            tradeoffs.append(f"долгий перелёт по оценке: {hours(flight)}")
        if d.visa_required is False:
            why.append("сохранённая справка указывает безвизовый режим; условия нужно перепроверить")
        elif d.visa_required is None:
            risks.append("Визовый режим неизвестен")
        if price is None:
            risks.append("Цена неизвестна; соответствие бюджету не подтверждено")
        elif brief.budget_rub is not None:
            diff = price - brief.budget_rub
            (tradeoffs if diff > 0 else why).append(
                f"справочная оценка дороже бюджета на {rub(diff)}"
                if diff > 0
                else f"справочная оценка ниже бюджета на {rub(-diff)}; полная цена требует поиска"
            )
        options.append(
            {
                "index": 0,
                "kind": "destination",
                "code": d.place.iata,
                "name": d.place.name,
                "country": d.country,
                "air_temperature_c": air,
                "water_temperature_c": water,
                "flight_minutes": flight,
                "visa_required": d.visa_required,
                "estimated_total_rub": price,
                "why": why,
                "tradeoffs": tradeoffs,
                "risks": list(dict.fromkeys(risks)),
                "is_synthetic": d.is_synthetic,
            }
        )

    def known(value: int | None) -> tuple[bool, int]:
        return value is None, value if value is not None else 0

    def rank(o: dict[str, Any]) -> tuple:
        if "short_flight" in brief.needs or "child" in brief.needs:
            return known(o["flight_minutes"]), known(o["estimated_total_rub"]), o["code"]
        return len(o["tradeoffs"]), known(o["estimated_total_rub"]), o["code"]

    result.options = sorted(options, key=rank)[:3]
    for i, option in enumerate(result.options, 1):
        option["index"] = i
    if not result.options:
        result.notes.append("По доступным данным нет направлений с подтверждёнными обязательными условиями")
    elif relaxed_budget:
        if any(o["estimated_total_rub"] is None for o in result.options):
            result.notes.append("Показаны справочные направления без подтверждённой цены; бюджет пока не проверен")
        elif all(o["estimated_total_rub"] > brief.budget_rub for o in result.options):
            result.notes.append("Справочные оценки найденных направлений превышают бюджет")
    return result


# ---------------------------------------------------------------------------
# Сборка поездки: рейсы, отель, трансфер, проверка стыковок
# ---------------------------------------------------------------------------


async def _pick_start_date(brief: TripBrief, call: ToolCaller, today: date, result: PlanResult) -> date | None:
    if brief.start_date:
        return brief.start_date
    assert brief.month and brief.origin and brief.destination
    days = await call(
        "price_calendar",
        {
            "origin": brief.origin,
            "destination": brief.destination,
            "month": _month_key(brief.month, today),
            "travelers": brief.travelers.model_dump(),
        },
    )
    if days is None:
        result.notes.append("Календарь цен недоступен; задайте конкретную дату или повторите поиск")
        return None
    future = [d for d in days or [] if d.date > today]
    if not future:
        return None
    best = min(future, key=lambda d: (d.price_rub, d.date))
    worst = max(d.price_rub for d in future)
    text = f"вылет {best.date.strftime('%d.%m')} — минимальная цена в доступном календаре перелётов в одну сторону"
    if worst > best.price_rub:
        text += f" (разница с максимальной ценой календаря {rub(worst - best.price_rub)}; не экономия всей поездки)"
    result.assumptions.append(Assumption("start_date", text))
    return best.date


def _hotel_fits(hotel: HotelOffer, brief: TripBrief) -> bool:
    """Явные требования к тарифу/звёздам подтверждаем; неизвестные правила детей/питомцев показываем как риск."""
    if brief.pets and hotel.pets_allowed is False:
        return False
    if brief.children_ages and hotel.adults_only is True:
        return False
    if brief.refundable_only and hotel.refundable is not True:
        return False
    if brief.min_stars is not None and (hotel.stars is None or hotel.stars < brief.min_stars):
        return False
    return True


def _flight_fits(flight: Flight, brief: TripBrief) -> bool:
    elapsed = (flight.arrival_at.astimezone(UTC) - flight.departure_at.astimezone(UTC)).total_seconds() / 60
    return (not brief.direct_only or flight.stops == 0) and (
        brief.max_flight_minutes is None or elapsed <= brief.max_flight_minutes
    )


def _issues_dump(issues: list[Issue]) -> list[dict[str, Any]]:
    return [
        {
            "rule": i.rule,
            "severity": i.severity,
            "message": i.message,
            "fixes": [
                {
                    "label": f.label,
                    "price_delta_rub": f.price_delta_rub,
                    "price_estimated": f.price_estimated,
                    "patch": f.patch,
                }
                for f in i.fixes
            ],
        }
        for i in issues
    ]


def _flight_dump(f: Flight) -> dict[str, Any]:
    return {
        "id": f.id,
        "carrier": f.carrier,
        "from": f.origin.iata,
        "to": f.destination.iata,
        "departure_at": f.departure_at.isoformat(),
        "arrival_at": f.arrival_at.isoformat(),
        "stops": f.stops,
        "price_rub": f.price_rub,
        "baggage_price_rub": f.baggage_price_rub,
        "is_overnight": f.is_overnight,
    }


@dataclass
class _Combo:
    outbound: Flight
    hotel: HotelOffer
    inbound: Flight
    transfer: Transfer | None
    issues: list[Issue]
    validation_ok: bool = True

    @property
    def total(self) -> int:
        return (
            self.outbound.price_rub
            + self.outbound.baggage_price_rub
            + self.inbound.price_rub
            + self.inbound.baggage_price_rub
            + self.hotel.price_rub
            + (self.transfer.price_rub if self.transfer else 0)
        )


def _score(c: _Combo, brief: TripBrief) -> int:
    """Меньше — лучше. Временная формула до R2: цена плюс штрафы за проблемы и несовпадения с желаниями."""
    score = c.total * (2 if "budget_low" in brief.needs else 1)
    score += sum(PENALTY[i.severity] for i in c.issues)
    if brief.budget_rub and c.total > brief.budget_rub:
        score += 2 * (c.total - brief.budget_rub)
    needs = set(brief.needs)
    if needs & {"romantic", "quiet"} and c.hotel.adults_only:
        score -= 8_000
    if "late_arrival" in needs and c.hotel.reception_24h is False:
        score += 10_000
    if "short_transfer" in needs and c.transfer and c.transfer.duration_minutes > 60:
        score += 8_000
    if c.hotel.rating:
        score -= int(c.hotel.rating * 1_000)
    return score


def _explain(c: _Combo, brief: TripBrief) -> tuple[list[str], list[str], list[str]]:
    """Факты для объяснения: почему подходит, чем пожертвовали, риски. Только из данных."""
    why: list[str] = []
    tradeoffs: list[str] = []
    risks: list[str] = []
    h = c.hotel
    if brief.budget_rub:
        diff = c.total - brief.budget_rub
        if diff <= 0:
            why.append(f"известные расходы ниже бюджета на {rub(-diff)}; прочие доплаты не оценены")
        else:
            tradeoffs.append(f"дороже бюджета на {rub(diff)}")
    if h.refundable is True:
        why.append("возвратный тариф отеля; сроки и удержания нужно уточнить")
    elif h.refundable is False:
        tradeoffs.append("отель без возврата денег при отмене")
    if c.outbound.stops == 0 and c.inbound.stops == 0:
        why.append("прямые рейсы")
    if brief.pets:
        if h.pets_allowed:
            why.append("с животными можно")
        else:
            risks.append("отель не указал, можно ли с животными, — нужно уточнить до оплаты")
    if brief.children_ages and h.adults_only is None:
        risks.append("неизвестно, принимает ли отель детей")
    if {"romantic", "quiet"} & set(brief.needs) and h.adults_only:
        why.append("отель только для взрослых")
    if h.reception_24h:
        why.append("ресепшен круглосуточно")
    if h.refundable is None:
        risks.append("Условия возврата неизвестны")
    if not c.validation_ok:
        risks.append("Проверка стыковок не завершена; подтверждение недоступно")
    risks.append("Обратный трансфер, виза и прочие обязательные доплаты не оценены")
    if c.transfer:
        t = c.transfer
        line = f"трансфер {hours(t.duration_minutes)} за {rub(t.price_rub)}"
        if not t.available and t.depart_at is not None:
            line += f", ближайшее отправление {t.depart_at:%d.%m %H:%M}"
        (why if t.duration_minutes <= 60 else tradeoffs).append(line)
    else:
        risks.append("Цена и время трансфера из аэропорта неизвестны; показана только известная часть расходов")
    if c.outbound.is_overnight:
        tradeoffs.append("ночной перелёт туда")
    for issue in c.issues:
        if issue.severity != "info":
            risks.append(issue.message)
    if c.outbound.baggage_price_rub or c.inbound.baggage_price_rub:
        why.append(
            f"модельная доплата за багаж включена: {rub(c.outbound.baggage_price_rub + c.inbound.baggage_price_rub)}"
        )
    return why, tradeoffs, risks


async def search_trip(brief: TripBrief, call: ToolCaller, today: date) -> PlanResult:
    result = PlanResult()
    assert brief.origin and brief.destination
    nights = brief.nights or DEFAULT_NIGHTS
    if brief.nights is None:
        result.assumptions.append(
            Assumption("nights", f"беру {plural(nights, 'ночь', 'ночи', 'ночей')} — поправьте, если нужно иначе")
        )
    start = await _pick_start_date(brief, call, today, result)
    if start is None:
        if not result.notes:
            result.notes.append("По доступному календарю нет рейсов в этом месяце")
        return result
    result.start_date = start
    travelers = brief.travelers.model_dump()

    outbound: list[Flight] | None = await call(
        "search_flights",
        {
            "origin": brief.origin,
            "destination": brief.destination,
            "departure_date": start.isoformat(),
            "travelers": travelers,
            "direct_only": brief.direct_only,
        },
    )
    if outbound is None:
        result.notes.append("Источник рейсов недоступен; поиск нужно повторить")
        return result
    outbound = [f for f in outbound if _flight_fits(f, brief)]
    if not outbound:
        result.notes.append(
            f"По доступным данным нет предложений {place_name(brief.origin)} → "
            f"{place_name(brief.destination)} на {start}"
        )
        return result

    combos: list[_Combo] = []
    # Ночной рейс прилетает на следующий день: заселение и обратный рейс считаем от даты прилёта.
    for check_in in sorted({f.arrival_at.date() for f in outbound}):
        try:
            check_out = check_in + timedelta(days=nights)
        except OverflowError:
            result.notes.append("Даты проживания выходят за поддерживаемый календарь")
            continue
        hotels: list[HotelOffer] | None = await call(
            "search_hotels",
            {
                "destination": brief.destination,
                "check_in": check_in.isoformat(),
                "check_out": check_out.isoformat(),
                "travelers": travelers,
                "min_stars": brief.min_stars or 0,
                "refundable_only": brief.refundable_only,
            },
        )
        inbound: list[Flight] | None = await call(
            "search_flights",
            {
                "origin": brief.destination,
                "destination": brief.origin,
                "departure_date": check_out.isoformat(),
                "travelers": travelers,
                "direct_only": brief.direct_only,
            },
        )
        if hotels is None or inbound is None:
            result.notes.append("Источник отелей или обратных рейсов недоступен; условия не проверены")
            continue
        inbound = [f for f in inbound if _flight_fits(f, brief)]
        suitable = [h for h in hotels or [] if _hotel_fits(h, brief)]
        if hotels and not suitable:
            result.notes.append("Найденные отели не подтверждают обязательные условия брифа")
        for out_flight in (f for f in outbound if f.arrival_at.date() == check_in):
            for hotel in suitable:
                transfer = await _transfer(out_flight, hotel, brief, call)
                for in_flight in inbound or []:
                    issues = _validate(out_flight, hotel, in_flight, transfer, brief)
                    combos.append(
                        _Combo(
                            out_flight,
                            hotel,
                            in_flight,
                            transfer,
                            issues or [],
                            validation_ok=issues is not None,
                        )
                    )
    if not combos:
        if not result.notes:
            result.notes.append("По доступным данным нет сочетания отеля и обратных рейсов с заданными условиями")
        return result

    if brief.budget_rub is not None:
        combos = [c for c in combos if c.total <= brief.budget_rub]
        if not combos:
            result.notes.append("Даже известные расходы превышают бюджет; подходящий вариант не подтверждён")
            return result
    result.options = _pick_packages(combos, brief)
    return result


async def _transfer(flight: Flight, hotel: HotelOffer, brief: TripBrief, call: ToolCaller) -> Transfer | None:
    pickup = (flight.arrival_at.astimezone(UTC) + timedelta(minutes=AIRPORT_EXIT_MIN)).astimezone(
        flight.arrival_at.tzinfo
    )
    found: list[Transfer] | None = await call(
        "get_transfer",
        {
            "airport": flight.destination.iata,
            "hotel_id": hotel.hotel_id,
            "pickup_at": pickup.isoformat(),
            "travelers": brief.travelers.model_dump(),
        },
    )
    if not found:
        return None
    # Инструмент уже сортирует: доступные и без запретов — первыми.
    resolved = [t for t in found if t.depart_at is not None]
    return resolved[0] if resolved else None


def _validate(
    outbound: Flight, hotel: HotelOffer, inbound: Flight, transfer: Transfer | None, brief: TripBrief
) -> list[Issue] | None:
    try:
        leg = transfer_leg(transfer) if transfer else None
        timeline = build_timeline(outbound, hotel, inbound, arrival_transfer=leg)
        return validate_trip(
            timeline,
            hotel,
            brief.travelers,
            arrival_transfer=leg,
            requirements=Requirements(refundable_required=brief.refundable_only),
        )
    except Exception:  # валидатор не должен ронять подбор; проблема видна в логах
        log.exception("Проверка стыковок не удалась")
        return None


def _pick_packages(combos: list[_Combo], brief: TripBrief) -> list[dict[str, Any]]:
    """До трёх разных отелей: лучший по оценке, самый комфортный и следующий по оценке.

    Самый дешёвый из показанных помечается отдельно, чтобы метка всегда говорила правду.
    """
    by_score = sorted(
        combos,
        key=lambda c: (
            c.transfer is None,
            not c.validation_ok,
            any(i.severity == "critical" for i in c.issues),
            _score(c, brief),
        ),
    )
    safe = [
        c
        for c in by_score
        if c.transfer is not None and c.validation_ok and not any(i.severity == "critical" for i in c.issues)
    ] or by_score
    picks: list[tuple[str, _Combo]] = [("Оптимальный", by_score[0])]

    def add(label: str, candidates: list[_Combo]) -> None:
        used = {c.hotel.hotel_id for _, c in picks}
        for c in candidates:
            if c.hotel.hotel_id not in used:
                picks.append((label, c))
                return

    add("Подешевле", sorted(safe, key=lambda c: (c.total, _score(c, brief))))
    add("Комфорт", sorted(safe, key=lambda c: (-(c.hotel.stars or 0), -(c.hotel.rating or 0), _score(c, brief))))
    cheapest = min(c.total for _, c in picks)
    picks = [(label, c) for label, c in picks if not (label == "Подешевле" and c.total > picks[0][1].total)] or picks
    picks = [(f"{label} · дешевле всех" if c.total == cheapest and len(picks) > 1 else label, c) for label, c in picks]

    packages = []
    for i, (label, c) in enumerate(picks, 1):
        why, tradeoffs, risks = _explain(c, brief)
        h = c.hotel
        packages.append(
            {
                "index": i,
                "kind": "package",
                "label": label,
                "destination": h.place.iata,
                "hotel": {
                    "hotel_id": h.hotel_id,
                    "offer_id": h.offer_id,
                    "name": h.name,
                    "stars": h.stars,
                    "rating": h.rating,
                    "check_in": h.check_in.isoformat(),
                    "check_out": h.check_out.isoformat(),
                    "nights": (h.check_out - h.check_in).days,
                    "price_rub": h.price_rub,
                    "refundable": h.refundable,
                    "adults_only": h.adults_only,
                    "pets_allowed": h.pets_allowed,
                    "review_profile": (
                        getattr(h, "review_profile", None).model_dump(mode="json")
                        if getattr(h, "review_profile", None) is not None
                        else None
                    ),
                },
                "outbound": _flight_dump(c.outbound),
                "inbound": _flight_dump(c.inbound),
                "transfer": (
                    {
                        "id": c.transfer.id,
                        "mode": c.transfer.mode,
                        "duration_minutes": c.transfer.duration_minutes,
                        "price_rub": c.transfer.price_rub,
                        "available": c.transfer.available,
                        "depart_at": c.transfer.depart_at.isoformat() if c.transfer.depart_at else None,
                    }
                    if c.transfer
                    else None
                ),
                "total_rub": c.total if c.transfer is not None else None,
                "known_total_rub": c.total,
                "price_scope": "round_trip_flights_baggage_hotel_arrival_transfer",
                "unpriced_components": ["return_transfer", "visa_and_other_required_extras"],
                "validation_status": "checked" if c.validation_ok else "failed",
                "can_confirm": (
                    c.transfer is not None
                    and c.validation_ok
                    and not any(issue.severity == "critical" for issue in c.issues)
                    and (not brief.pets or h.pets_allowed is True)
                    and (not brief.children_ages or h.adults_only is False)
                ),
                "issues": _issues_dump(c.issues),
                "why": why,
                "tradeoffs": tradeoffs,
                "risks": risks,
                "is_synthetic": True,
            }
        )
    return packages


def needs_summary(brief: TripBrief) -> list[str]:
    """Что агент учёл из скрытых требований — для плашек брифа и ответа пользователю."""
    return [f"{NEEDS[n].label}: {NEEDS[n].effect}" for n in brief.needs if n in NEEDS]


async def search_train_trip(brief: TripBrief, call: ToolCaller) -> PlanResult:
    """D6 quotes one-way rail journeys, not a flight/hotel package or a reservation."""
    assert brief.origin and brief.destination and brief.start_date
    result = PlanResult(start_date=brief.start_date)
    trains: list[Train] | None = await call(
        "search_trains",
        {
            "origin": brief.origin,
            "destination": brief.destination,
            "departure_date": brief.start_date.isoformat(),
            "travelers": brief.travelers.model_dump(),
            "seat_class": brief.seat_class,
        },
    )
    if trains is None:
        result.notes.append("Источник поездов недоступен; повторите поиск")
        return result
    matching = [t for t in trains if brief.budget_rub is None or t.price_rub <= brief.budget_rub]
    for index, train in enumerate(sorted(matching, key=lambda t: (t.price_rub, t.departure_at))[:3], 1):
        result.options.append({**train.model_dump(mode="json"), "index": index, "kind": "train"})
    if not result.options:
        result.notes.append("В доступных данных нет поездов по заданному маршруту, классу мест и бюджету")
    result.notes.append(
        "Цены за всю группу в одну сторону; стоимость модельная. Наличие мест и бронирование не проверены"
    )
    return result
