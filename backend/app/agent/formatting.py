"""Тексты по шаблонам: когда модель недоступна, работает фейковый провайдер или текст должен быть точным.

Подтверждение покупки и чек всегда пишет код: там цены, которые нельзя пересказывать своими словами.
"""

from datetime import date
from typing import Any

from app.agent.brief import FIELD_LABELS, ApplyResult, TripBrief
from app.agent.places import place_name
from app.agent.planner import Assumption, PlanResult, hours, plural, rub

_MONTH_NAMES = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)  # fmt: skip
_MONTHS_IN = (
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
)  # fmt: skip


def quick_replies(field: str, today: date) -> list[str]:
    """Кнопки быстрых ответов для вопроса. Полная политика уточнений — A5 (#25)."""
    if field == "month":
        return [_MONTH_NAMES[(today.month - 1 + i) % 12].capitalize() for i in range(1, 4)]
    if field == "origin":
        return ["Из Москвы", "Из Санкт-Петербурга"]
    if field == "children_ages":
        return ["До 2 лет", "3 года", "7 лет", "12 лет"]
    return []


def question(field: str, brief: TripBrief) -> str:
    known = understood(brief)
    prefix = f"Понял: {known}. " if known else ""
    texts = {
        "month": "Когда хотите поехать? Достаточно месяца, точную дату подберу по ценам.",
        "origin": "Откуда полетите?",
        "children_ages": "Сколько лет ребёнку? От возраста зависят цена билета и подходящие отели.",
    }
    return prefix + texts.get(field, f"Уточните, пожалуйста: {FIELD_LABELS.get(field, field)}.")


def understood(brief: TripBrief) -> str:
    if brief == TripBrief():
        return ""  # пока ничего не известно — нечего пересказывать
    parts = []
    if brief.destination:
        parts.append(place_name(brief.destination))
    if brief.start_date:
        parts.append(f"вылет {brief.start_date.strftime('%d.%m')}")
    elif brief.month:
        parts.append(f"в {_MONTHS_IN[brief.month - 1]}")
    if brief.nights:
        parts.append(plural(brief.nights, "ночь", "ночи", "ночей"))
    parts.append(group(brief))
    if brief.budget_rub:
        parts.append(f"до {rub(brief.budget_rub)}")
    return ", ".join(parts)


def group(brief: TripBrief) -> str:
    text = f"{brief.adults} {'взрослый' if brief.adults == 1 else 'взрослых'}"
    if brief.children_ages:
        text += f" + дети ({', '.join(map(str, brief.children_ages))} лет)"
    elif "child" in brief.needs:
        text += " + ребёнок"
    if brief.pets:
        text += " + питомец"
    return text


def assumptions_line(assumptions: list[Assumption]) -> str:
    if not assumptions:
        return ""
    return "Допущения: " + "; ".join(a.text for a in assumptions) + "."


def rejected_line(applied: ApplyResult | None) -> str:
    if not applied or not applied.rejected:
        return ""
    notes = "; ".join(f"{FIELD_LABELS.get(r.field, r.field)} «{r.value}» — {r.reason}" for r in applied.rejected)
    return f"Не смог принять: {notes}."


def destinations(plan: PlanResult) -> str:
    if not plan.options:
        return " ".join(plan.notes) + " Попробуйте другой месяц или снимите часть ограничений."
    lines = ["Вот куда можно поехать:"]
    for o in plan.options:
        lines.append(
            f"{o['index']}. {o['name']} ({o['country']}) — около {rub(o['estimated_total_rub'])}, "
            f"лететь {hours(o['flight_minutes'])}, воздух +{o['air_temperature_c']:g} °C, "
            f"вода +{o['water_temperature_c']:g} °C."
        )
        if o["why"]:
            lines.append("   Подходит: " + "; ".join(o["why"]) + ".")
        if o["tradeoffs"]:
            lines.append("   Минусы: " + "; ".join(o["tradeoffs"]) + ".")
    lines += [*plan.notes, assumptions_line(plan.assumptions)]
    lines.append("Данные демонстрационные. Какое направление собрать в поездку?")
    return "\n".join(line for line in lines if line)


def packages(plan: PlanResult, diff: str = "") -> str:
    if not plan.options:
        return " ".join(plan.notes) + " Попробуйте другие даты или направление."
    lines = [diff] if diff else []
    lines.append("Собрал варианты поездки целиком: перелёт туда и обратно, отель и трансфер.")
    for o in plan.options:
        lines.append(f"{o['index']}. {o['label']}: {o['hotel']['name']} — {rub(o['total_rub'])} за всех.")
        lines.append(
            f"   Отель {plural(o['hotel']['nights'], 'ночь', 'ночи', 'ночей')} с {day(o['hotel']['check_in'])}, "
            + flight_line(o)
        )
        for title, key in (("Почему", "why"), ("Чем жертвуем", "tradeoffs"), ("Риски", "risks")):
            if o[key]:
                lines.append(f"   {title}: " + "; ".join(o[key]) + ".")
    lines += [*plan.notes, assumptions_line(plan.assumptions)]
    lines.append("Цены и наличие демонстрационные. Какой вариант оформить? Или поменяем условия.")
    return "\n".join(line for line in lines if line)


def when(iso: str) -> str:
    """'2026-11-07T09:00:00+03:00' → '07.11 09:00' (местное время точки)."""
    return f"{iso[8:10]}.{iso[5:7]} {iso[11:16]}"


def day(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}"


def flight_line(o: dict[str, Any]) -> str:
    return f"вылет {when(o['outbound']['departure_at'])}, обратно {when(o['inbound']['departure_at'])}."


def which_option(count: int) -> str:
    return f"Какой вариант выбираете? Напишите номер от 1 до {count}."


def confirmation(o: dict[str, Any], brief: TripBrief) -> str:
    lines = [
        f"Проверьте перед оформлением — вариант {o['index']} «{o['label']}»:",
        f"• Туда: {o['outbound']['from']} → {o['outbound']['to']}, {when(o['outbound']['departure_at'])}"
        f" — {rub(o['outbound']['price_rub'])}",
        f"• Отель: {o['hotel']['name']}, {day(o['hotel']['check_in'])} — {day(o['hotel']['check_out'])}"
        f" — {rub(o['hotel']['price_rub'])}"
        + (", бесплатная отмена" if o["hotel"]["refundable"] else ", без возврата"),
    ]
    if o["transfer"]:
        t = o["transfer"]
        lines.append(f"• Трансфер: {hours(t['duration_minutes'])} — {rub(t['price_rub'])}")
    lines += [
        f"• Обратно: {o['inbound']['from']} → {o['inbound']['to']}, {when(o['inbound']['departure_at'])}"
        f" — {rub(o['inbound']['price_rub'])}",
        f"Итого за всех ({group(brief)}): {rub(o['total_rub'])}.",
    ]
    critical = [i["message"] for i in o["issues"] if i["severity"] == "critical"]
    if critical:
        lines.append("Внимание: " + "; ".join(critical) + ".")
    lines.append("Оформляем? Это имитация: деньги не списываются.")
    return "\n".join(lines)


def booked(booking: dict[str, Any]) -> str:
    return (
        f"Готово! Демо-бронь {booking['id']}: {booking['hotel']}, итого {rub(booking['total_rub'])}. "
        "Это имитация оплаты, реального бронирования не было."
    )


def cancelled() -> str:
    return "Хорошо, не оформляю. Можно выбрать другой вариант или поменять условия."


def off_topic() -> str:
    return "Я помогаю спланировать поездку. Расскажите, куда и когда хотите поехать — или что вам важно в отдыхе."
