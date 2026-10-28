"""D8 date comparisons for the deterministic chat, without changing its brief.

Only like-for-like known components are compared. The return flight, required
baggage and every hotel night are mandatory; missing provider data never becomes
a synthetic offer or a zero price. The unpriced remainder is shown explicitly.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.agent.brief import TripBrief
from app.agent.planner import ToolCaller, _flight_fits, _hotel_fits, _validate, rub
from app.agent.session import Session
from app.booking.timeline import AIRPORT_EXIT_MIN
from app.contracts.tools import Flight, HotelOffer, Transfer
from app.tools.price_calendar import TripQuote, suggest_date_shifts

_COMMAND = re.compile(r"\b(?:перенеси|перенес[её]м|сдвинь|сдвигаем|измени|поменяй|хочу\s+(?:вылет|на))\b")
_SHIFT = re.compile(r"\b(?:раньше|позже|сдвиг\w*|сдвину\w*|перенос\w*|перенести|дат\w*)\b")
_COMPARE = re.compile(r"дешев|эконом|выгодн|сравн|\b(?:а|что)\s+если\b|сколько\s+(?:будет|стоит)")
_DAYS = re.compile(r"(?:на\s+)?(один|одного|два|двух|три|тр[её]х|\d+)\s+(?:день|дня|дней)\b")
_NUMBERS = {"один": 1, "одного": 1, "два": 2, "двух": 2, "три": 3, "трех": 3, "трёх": 3}
_UNPRICED = ["return_transfer", "visa_and_other_required_extras"]


@dataclass(frozen=True)
class DateSavingsComparison:
    text: str
    facts: dict[str, Any]


def is_date_savings_question(text: str) -> bool:
    """Separate a comparison question from an instruction to move the trip."""
    normalized = text.casefold()
    return bool(_SHIFT.search(normalized) and _COMPARE.search(normalized) and not _COMMAND.search(normalized))


def _offsets(text: str) -> tuple[int, ...] | None:
    normalized = text.casefold()
    amount = None
    if match := _DAYS.search(normalized):
        amount = int(match[1]) if match[1].isdigit() else _NUMBERS[match[1]]
    elif re.search(r"\bна\s+день\b", normalized):
        amount = 1
    # A numeric interval (1–3 days) means the supported complete window.
    if re.search(r"[123]\s*[-–—]\s*[123]\s+дн", normalized):
        amount = None
    if amount is not None:
        if not 1 <= amount <= 3:
            return None
        if "раньше" in normalized and "позже" not in normalized:
            return (-amount,)
        if "позже" in normalized and "раньше" not in normalized:
            return (amount,)
        return (-amount, amount)
    if "раньше" in normalized and "позже" not in normalized:
        return (-3, -2, -1)
    if "позже" in normalized and "раньше" not in normalized:
        return (1, 2, 3)
    return (-3, -2, -1, 1, 2, 3)


def _source(value: Any) -> str:
    if isinstance(value, dict) and value.get("provider") == "aviasales_data_api":
        return "aviasales_data_api"
    return "synthetic"


def _flight_source(value: Flight) -> str:
    return "aviasales_data_api" if value.price_source is not None else "synthetic"


def _code(value: str) -> str:
    return "SVO" if value == "MOW" else value


def _unavailable(reason: str, text: str) -> DateSavingsComparison:
    return DateSavingsComparison(
        text=text,
        facts={
            "comparison_status": "unavailable",
            "reason": reason,
            "suggestions": [],
            "state_changed": False,
        },
    )


@dataclass(frozen=True)
class _Baseline:
    quote: TripQuote
    package: dict[str, Any]
    option: int
    assumption: str | None
    include_transfer: bool
    source: str


def _baseline(session: Session, text: str) -> _Baseline | DateSavingsComparison:
    if session.stale:
        return _unavailable("stale_options", "Условия поездки изменились. Сначала нужно обновить варианты и их цены.")
    if not session.packages:
        return _unavailable(
            "no_packages", "Сначала найдите авиапоездку с отелем: для сравнения нужны оба рейса и проживание."
        )
    normalized = text.casefold()
    explicit = re.search(r"\bвариант(?:а|у|ом|е)?\s*(?:№\s*)?([1-9]\d*)\b", normalized) or re.search(
        r"\b([1-9]\d*)\s*вариант", normalized
    )
    ordinal = re.search(r"\b(перв\w*|втор\w*|трет\w*)\s+вариант", normalized)
    named_option = next(
        (
            number
            for prefix, number in (("перв", 1), ("втор", 2), ("трет", 3))
            if ordinal and ordinal[1].startswith(prefix)
        ),
        None,
    )
    option = int(explicit[1]) if explicit else named_option or session.selected or 1
    package = session.package(option)
    if package is None:
        return _unavailable(
            "unknown_option", "Такого варианта нет среди показанных. Укажите номер существующего варианта."
        )
    assumption = (
        None
        if explicit or named_option or session.selected is not None
        else f"За основу взят вариант {option} из показанных."
    )
    try:
        outbound, inbound, hotel = (package[key] for key in ("outbound", "inbound", "hotel"))
        departure = datetime.fromisoformat(outbound["departure_at"]).date()
        check_in, check_out = (date.fromisoformat(hotel[key]) for key in ("check_in", "check_out"))
        nights = (check_out - check_in).days
        if (
            not session.brief.origin
            or not session.brief.destination
            or outbound["from"] != _code(session.brief.origin)
            or outbound["to"] != _code(session.brief.destination)
            or inbound["from"] != outbound["to"]
            or inbound["to"] != outbound["from"]
            or datetime.fromisoformat(outbound["arrival_at"]).date() != check_in
            or datetime.fromisoformat(inbound["departure_at"]).date() != check_out
            or session.brief.start_date not in (None, departure)
            or session.brief.nights not in (None, nights)
            or any(issue.get("severity") == "critical" for issue in package.get("issues", []))
            or package.get("validation_status") == "failed"
        ):
            raise ValueError("Incomparable baseline")
        sources = {_source(leg.get("price_source")) for leg in (outbound, inbound)}
        if len(sources) != 1:
            raise ValueError("Mixed flight sources")
        transfer = package.get("transfer")
        quote = TripQuote(
            departure_date=departure,
            nights=nights,
            origin=outbound["from"],
            destination=outbound["to"],
            hotel_id=hotel["hotel_id"],
            travelers=session.brief.travelers,
            flight_total_rub=sum(leg["price_rub"] + leg["baggage_price_rub"] for leg in (outbound, inbound)),
            hotel_total_rub=hotel["price_rub"],
            other_total_rub=transfer["price_rub"] if transfer else 0,
            is_synthetic=True,
        )
        if quote.total_rub != package["known_total_rub"]:
            raise ValueError("Baseline components differ from the displayed amount")
    except (KeyError, TypeError, ValueError, OverflowError):
        return _unavailable(
            "incomplete_baseline",
            "Не могу сопоставить все цены и условия выбранного варианта. Обновите поиск перед сравнением дат.",
        )
    return _Baseline(quote, package, option, assumption, transfer is not None, sources.pop())


def _matching_flights(rows: Any, brief: TripBrief, day: date, *, inbound: bool, source: str) -> list[Flight]:
    origin, destination = (_code(brief.origin), _code(brief.destination))
    if inbound:
        origin, destination = destination, origin
    return [
        flight
        for flight in rows or []
        if isinstance(flight, Flight)
        and flight.origin.iata == origin
        and flight.destination.iata == destination
        and flight.departure_at.date() == day
        and _flight_source(flight) == source
        and _flight_fits(flight, brief)
        and (inbound or not brief.late_departure or flight.departure_at.hour >= 18 or flight.departure_at.hour < 6)
    ]


async def _quote_day(
    day: date, baseline: _Baseline, brief: TripBrief, call: ToolCaller
) -> tuple[TripQuote | None, str]:
    travelers = brief.travelers.model_dump()
    common = {"travelers": travelers, "direct_only": brief.direct_only}
    rows = await call(
        "search_flights",
        {**common, "origin": brief.origin, "destination": brief.destination, "departure_date": day.isoformat()},
    )
    if rows is None:
        return None, "source_unavailable"
    outbound = _matching_flights(rows, brief, day, inbound=False, source=baseline.source)
    candidates: list[TripQuote] = []
    source_failed = False
    for check_in in sorted({flight.arrival_at.date() for flight in outbound}):
        try:
            check_out = check_in + timedelta(days=baseline.quote.nights)
        except OverflowError:
            continue
        hotel_rows = await call(
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
        return_rows = await call(
            "search_flights",
            {
                **common,
                "origin": brief.destination,
                "destination": brief.origin,
                "departure_date": check_out.isoformat(),
            },
        )
        if hotel_rows is None or return_rows is None:
            source_failed = True
            continue
        hotels = [
            hotel
            for hotel in hotel_rows
            if isinstance(hotel, HotelOffer)
            and hotel.hotel_id == baseline.quote.hotel_id
            and hotel.check_in == check_in
            and hotel.check_out == check_out
            and hotel.place.iata == _code(brief.destination)
            and hotel.refundable is baseline.package["hotel"].get("refundable")
            and _hotel_fits(hotel, brief)
        ]
        inbound = _matching_flights(return_rows, brief, check_out, inbound=True, source=baseline.source)
        for flight in (item for item in outbound if item.arrival_at.date() == check_in):
            for hotel in hotels:
                transfers: list[Transfer | None] = [None]
                if baseline.include_transfer:
                    pickup = (flight.arrival_at.astimezone(UTC) + timedelta(minutes=AIRPORT_EXIT_MIN)).astimezone(
                        flight.arrival_at.tzinfo
                    )
                    transfer_rows = await call(
                        "get_transfer",
                        {
                            "airport": flight.destination.iata,
                            "hotel_id": hotel.hotel_id,
                            "pickup_at": pickup.isoformat(),
                            "travelers": travelers,
                        },
                    )
                    if transfer_rows is None:
                        source_failed = True
                    transfers = [
                        transfer
                        for transfer in transfer_rows or []
                        if isinstance(transfer, Transfer)
                        and transfer.destination.id == hotel.hotel_id
                        and transfer.origin.iata == flight.destination.iata
                        and transfer.pickup_at.astimezone(UTC) == pickup.astimezone(UTC)
                        and transfer.depart_at is not None
                        and not {"pets_forbidden", "child_seat_missing"}.intersection(transfer.issues)
                    ]
                for back in inbound:
                    for transfer in transfers:
                        issues = _validate(flight, hotel, back, transfer, brief)
                        if issues is None or any(issue.severity == "critical" for issue in issues):
                            continue
                        candidates.append(
                            baseline.quote.model_copy(
                                update={
                                    "departure_date": day,
                                    "flight_total_rub": flight.price_rub
                                    + flight.baggage_price_rub
                                    + back.price_rub
                                    + back.baggage_price_rub,
                                    "hotel_total_rub": hotel.price_rub,
                                    "other_total_rub": transfer.price_rub if transfer else 0,
                                }
                            )
                        )
    return min(candidates, key=lambda quote: quote.total_rub, default=None), (
        "source_unavailable" if source_failed and not candidates else "missing_or_incompatible_quotes"
    )


async def compare_date_savings(
    session: Session,
    call: ToolCaller,
    *,
    today: date,
    text: str = "Есть ли даты дешевле при сдвиге на 1–3 дня?",
) -> DateSavingsComparison:
    """Compare the current option without adopting dates or touching confirmation."""
    baseline = _baseline(session, text)
    if isinstance(baseline, DateSavingsComparison):
        return baseline
    offsets = _offsets(text)
    if offsets is None:
        return _unavailable("unsupported_shift", "Сейчас могу сравнить сдвиг на 1–3 дня раньше или позже.")
    checked: list[dict[str, Any]] = []
    brief = session.brief.model_copy(deep=True)

    async def quote_trip(day: date) -> TripQuote | None:
        offset = (day - baseline.quote.departure_date).days
        if offset not in offsets:
            return None
        if day <= today:
            checked.append({"departure_date": day.isoformat(), "shift_days": offset, "status": "past_date"})
            return None
        try:
            quote, reason = await _quote_day(day, baseline, brief, call)
        except Exception as exc:
            # Provider exceptions can contain request headers; never expose their text.
            checked.append(
                {
                    "departure_date": day.isoformat(),
                    "shift_days": offset,
                    "status": "source_error",
                    "error_type": type(exc).__name__,
                }
            )
            return None
        checked.append(
            {
                "departure_date": day.isoformat(),
                "shift_days": offset,
                "status": "quoted" if quote else reason,
                **({"quote": quote.model_dump(mode="json"), "known_total_rub": quote.total_rub} if quote else {}),
            }
        )
        return quote

    savings = await suggest_date_shifts(baseline.quote, quote_trip)
    include_transfer = baseline.include_transfer
    scope = "round_trip_flights_baggage_hotel" + ("_arrival_transfer" if include_transfer else "")
    unpriced = [*(_UNPRICED), *([] if include_transfer else ["arrival_transfer"])]
    confirmed = sum(item["status"] == "quoted" for item in checked)
    facts = {
        "comparison_status": "compared" if confirmed else "unavailable",
        "baseline_option": baseline.option,
        "baseline_assumption": baseline.assumption,
        "baseline": baseline.quote.model_dump(mode="json"),
        "baseline_known_total_rub": baseline.quote.total_rub,
        "hotel_id": baseline.quote.hotel_id,
        "flight_source": baseline.source,
        "price_scope": scope,
        "unpriced_components": unpriced,
        "minimum_saving_percent": 5,
        "strict_threshold": True,
        "checked_dates": checked,
        "suggestions": [item.model_dump(mode="json") for item in savings],
        "state_changed": False,
    }
    parts = [baseline.assumption] if baseline.assumption else []
    components = "оба перелёта с багажом и все ночи в том же отеле"
    if include_transfer:
        components += ", трансфер аэропорт → отель"
    parts.append(
        f"Сравнение для {baseline.quote.travelers.count} путешественников: {components}; "
        f"{baseline.quote.nights} ночей. Исходные известные расходы: {rub(baseline.quote.total_rub)}."
    )
    if savings:
        parts.append("Варианты с экономией строго больше 5% по этим компонентам:")
        for item in savings[:3]:
            direction = "раньше" if item.shift_days < 0 else "позже"
            parts.append(
                f"• Вылет {item.suggested_departure:%d.%m.%Y}, на {abs(item.shift_days)} дн. {direction}: "
                f"{rub(item.suggested_total_rub)}, разница {rub(item.saving_rub)} "
                f"({item.saving_percent}%; до округления больше 5%)."
            )
    elif confirmed:
        parts.append("Среди проверенных дат нет варианта с экономией строго больше 5% по известным расходам.")
    else:
        parts.append("Для выбранного сдвига нет полного набора сопоставимых котировок. Экономию подтвердить нельзя.")
    missing = len(checked) - confirmed
    if missing and confirmed:
        parts.append(f"Для {missing} дат сравнение не завершено: часть котировок отсутствует или источник недоступен.")
    extras = "обратный трансфер, виза и прочие обязательные доплаты"
    if not include_transfer:
        extras = "оба трансфера, виза и прочие обязательные доплаты"
    parts.append(f"Не оценены {extras}; полная стоимость поездки и экономия по ней не подтверждены.")
    if baseline.source == "aviasales_data_api":
        parts.append(
            "Авиабаза — котировки из поискового кэша Aviasales; наличие мест не подтверждено. "
            "Цена группы, багаж, отель и трансфер рассчитаны моделью."
        )
    else:
        parts.append("Это модельные цены из демо-данных; наличие мест и покупка не проверены.")
    parts.append("Это сравнение: даты, выбранный вариант и бронирование не изменены.")
    return DateSavingsComparison("\n".join(parts), facts)
