"""Пересборка поездки после сбоя перелёта: новый рейс, отель, трансфер и цена последствий.

Работает с вариантом поездки в том виде, в каком его собирает app/agent/planner.py
(словарь с outbound, inbound, hotel, transfer), и с инструментом search_flights из app/tools.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.protection.models import Action, CostItem, Disruption

ToolCaller = Callable[[str, dict[str, Any]], Awaitable[Any | None]]

# Минимальный запас между отменённым рейсом и новым: успеть получить уведомление и переоформить билет.
REBOOK_LEAD = timedelta(hours=2)
# Позже этого часа (местного) отель предупреждаем о позднем заезде, иначе бронь могут снять как no-show.
LATE_CHECK_IN_HOUR = 22


@dataclass
class Recovery:
    old_flight: dict[str, Any]
    new_flight: dict[str, Any] | None
    actions: list[Action] = field(default_factory=list)
    costs: list[CostItem] = field(default_factory=list)
    hotel_change: dict[str, Any] | None = None
    transfer_change: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _nights(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} ночь"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} ночи"
    return f"{n} ночей"


def _when(value: datetime) -> str:
    return value.strftime("%d.%m %H:%M")


def _flight_dump(f: Any) -> dict[str, Any]:
    return {
        "id": f.id,
        "carrier": f.carrier,
        "from": f.origin.iata,
        "to": f.destination.iata,
        "departure_at": f.departure_at.isoformat(),
        "arrival_at": f.arrival_at.isoformat(),
        "stops": f.stops,
        "price_rub": f.price_rub,
    }


async def _replacement(old: dict[str, Any], travelers: dict[str, Any], call: ToolCaller) -> dict[str, Any] | None:
    """Ближайший рейс того же направления не раньше, чем через REBOOK_LEAD после отменённого."""
    departure = _dt(old["departure_at"])
    earliest = departure + REBOOK_LEAD
    for shift in range(3):  # тот же день и два следующих
        day = (departure + timedelta(days=shift)).date()
        found = await call(
            "search_flights",
            {
                "origin": old["from"],
                "destination": old["to"],
                "departure_date": day.isoformat(),
                "travelers": travelers,
            },
        )
        candidates = sorted((f for f in found or [] if f.departure_at >= earliest), key=lambda f: f.departure_at)
        if candidates:
            return _flight_dump(candidates[0])
    return None


def _delayed(old: dict[str, Any], minutes: int) -> dict[str, Any]:
    shift = timedelta(minutes=minutes)
    return {
        **old,
        "departure_at": (_dt(old["departure_at"]) + shift).isoformat(),
        "arrival_at": (_dt(old["arrival_at"]) + shift).isoformat(),
        "price_rub": 0,  # тот же билет, доплаты нет
    }


async def plan(
    package: dict[str, Any], disruption: Disruption, travelers: dict[str, Any], call: ToolCaller
) -> Recovery:
    old = dict(package[disruption.leg])
    hotel = package["hotel"]
    nights = max(1, int(hotel.get("nights") or 1))
    nightly = round(hotel["price_rub"] / nights)
    route = f"{old['from']} → {old['to']}"

    if disruption.kind == "cancel":
        new = await _replacement(old, travelers, call)
        r = Recovery(old_flight=old, new_flight=new)
        cancelled_at = _when(_dt(old["departure_at"]))
        r.actions.append(Action(who="Авиакомпания", text=f"Зафиксировал отмену рейса {route} {cancelled_at}"))
        if new is None:
            r.notes.append("Подходящего рейса на ближайшие три дня нет — передаю случай оператору поддержки.")
            return r
        r.actions.append(
            Action(
                who="Авиакомпания",
                text=f"Нашёл замену: {new['carrier']}, вылет {_when(_dt(new['departure_at']))}, "
                f"прилёт {_when(_dt(new['arrival_at']))}",
            )
        )
        r.costs.append(CostItem(code="new_ticket", label=f"Новый билет {route}", amount_rub=new["price_rub"]))
        r.notes.append("Возврат за отменённый билет авиакомпания перечислит страховой, а не клиенту.")
    else:
        new = _delayed(old, disruption.delay_minutes)
        r = Recovery(old_flight=old, new_flight=new)
        hours = disruption.delay_minutes // 60
        r.actions.append(
            Action(
                who="Авиакомпания",
                text=f"Зафиксировал задержку рейса {route} на {hours} ч: вылет {_when(_dt(new['departure_at']))}",
            )
        )

    old_arrival, new_arrival = _dt(old["arrival_at"]), _dt(new["arrival_at"])
    old_departure, new_departure = _dt(old["departure_at"]), _dt(new["departure_at"])

    if disruption.leg == "outbound":
        check_in = datetime.fromisoformat(hotel["check_in"]).date()
        late_days = (new_arrival.date() - max(check_in, old_arrival.date())).days
        lost = min(max(0, late_days), nights - 1)
        if lost:
            r.hotel_change = {
                "hotel": hotel["name"],
                "lost_nights": lost,
                "new_check_in": new_arrival.date().isoformat(),
            }
            r.actions.append(
                Action(
                    who="Отель",
                    text=f"{hotel['name']}: предупредил о заезде {new_arrival.strftime('%d.%m')}, бронь сохранена",
                )
            )
            r.costs.append(
                CostItem(
                    code="lost_nights",
                    label=f"Сгоревшая бронь в отеле: {_nights(lost)}",
                    amount_rub=nightly * lost,
                )
            )
        elif new_arrival.hour >= LATE_CHECK_IN_HOUR or new_arrival.date() > old_arrival.date():
            r.actions.append(Action(who="Отель", text=f"{hotel['name']}: предупредил о позднем заезде"))

        transfer = package.get("transfer")
        if transfer and new_arrival != old_arrival:
            r.transfer_change = {"id": transfer["id"], "new_pickup_at": new_arrival.isoformat()}
            r.actions.append(Action(who="Трансфер", text=f"Перенёс встречу в аэропорту на {_when(new_arrival)}"))
            if new_arrival.date() != old_arrival.date():
                # Трансфер, перенесённый меньше чем за сутки, обычно не возвращают — оформляем новый.
                r.costs.append(CostItem(code="transfer", label="Повторный трансфер", amount_rub=transfer["price_rub"]))
    else:
        extra = (new_departure.date() - old_departure.date()).days
        if extra > 0:
            r.hotel_change = {"hotel": hotel["name"], "extra_nights": extra}
            r.actions.append(Action(who="Отель", text=f"{hotel['name']}: продлил проживание на {_nights(extra)}"))
            label = f"Дополнительно в отеле: {_nights(extra)}"
            r.costs.append(CostItem(code="extra_nights", label=label, amount_rub=nightly * extra))

    return r
