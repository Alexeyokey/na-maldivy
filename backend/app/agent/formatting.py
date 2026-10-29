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
        return ["1 год", "3 года", "7 лет", "12 лет"]
    return []


def question(field: str, brief: TripBrief) -> str:
    known = understood(brief)
    prefix = f"Понял: {known}. " if known else ""
    texts = {
        "month": "Когда хотите поехать? Достаточно месяца, точную дату подберу по ценам.",
        "origin": "Откуда отправитесь?" if brief.transport == "train" else "Откуда полетите?",
        "destination": "В какой город поедете?",
        "start_date": "На какую дату искать поезд? Укажите день, месяц и при необходимости год.",
        "children_ages": (
            "Укажите возраст каждого ребёнка, например: 5 и 8 лет. "
            + (
                "От возраста зависят цена билета и условия поездки."
                if brief.transport == "train"
                else "От возраста зависят цена билета и подходящие отели."
            )
        ),
    }
    return prefix + texts.get(field, f"Уточните, пожалуйста: {FIELD_LABELS.get(field, field)}.")


def understood(brief: TripBrief) -> str:
    if brief == TripBrief():
        return ""  # пока ничего не известно — нечего пересказывать
    parts = []
    if brief.destination:
        parts.append(place_name(brief.destination))
    if brief.has_flexible_dates and brief.start_date is None:
        if brief.departure_window_start and brief.departure_window_end:
            parts.append(
                f"вылет {day(brief.departure_window_start.isoformat())}–{day(brief.departure_window_end.isoformat())}"
            )
    elif brief.start_date:
        verb = "отправление" if brief.transport == "train" else "вылет"
        parts.append(f"{verb} {brief.start_date.strftime('%d.%m')}")
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
        text += " + " + plural(brief.pets, "питомец", "питомца", "питомцев")
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
    lines = ["Справочные направления; полная цена и наличие не подтверждены:"]
    for o in plan.options:
        price = "цена неизвестна" if o["estimated_total_rub"] is None else f"оценка {rub(o['estimated_total_rub'])}"
        flight = (
            "время перелёта неизвестно"
            if o["flight_minutes"] is None
            else f"лететь примерно {hours(o['flight_minutes'])}"
        )
        air = "воздух: нет данных" if o["air_temperature_c"] is None else f"воздух {o['air_temperature_c']:+g} °C"
        water = "вода: нет данных" if o["water_temperature_c"] is None else f"вода {o['water_temperature_c']:+g} °C"
        lines.append(f"{o['index']}. {o['name']} ({o['country']}) — {price}, {flight}, {air}, {water}.")
        lines.append(
            "   Доступен подбор поездки; наличие и состав нужно проверить."
            if o.get("full_trip_supported") is True
            else "   Только справка: подбор отеля и трансфера для этого направления пока недоступен."
        )
        for title, key in (("Подходит", "why"), ("Минусы", "tradeoffs"), ("Риски", "risks")):
            if o[key]:
                lines.append(f"   {title}: " + "; ".join(o[key]) + ".")
    lines += [*plan.notes, assumptions_line(plan.assumptions)]
    lines.append("Выберите подбор поездки или справку о направлении. Полная цена и наличие не подтверждены.")
    return "\n".join(line for line in lines if line)


def flexible_packages(plan: PlanResult, brief: TripBrief) -> str:
    """Exact, compact price-bearing response for an explicitly flexible search."""
    if not plan.options:
        return " ".join(plan.notes) + " Можно изменить бюджет, период или направление."
    summary = plan.search_summary or {}
    lines = [f"Рассчитал {len(plan.options)} поездки: {group(brief)}, из {place_name(brief.origin)}."]
    for option in plan.options:
        hotel = option["hotel"]
        departure = date.fromisoformat(option["outbound"]["departure_at"][:10])
        lines.append(
            f"{option['index']}. **{place_name(option['destination'])} — {rub(option['known_total_rub'])} за группу.** "
            f"Вылет {departure:%d.%m.%Y}, проживание до {date.fromisoformat(hotel['check_out']):%d.%m.%Y}, "
            f"{plural(hotel['nights'], 'ночь', 'ночи', 'ночей')}. {hotel['name']}."
        )
    if brief.nights is None or any(item.field == "nights" for item in plan.assumptions):
        lines.append("Для расчёта взял 7 ночей; длительность можно изменить.")
    if summary:
        lines.append(
            f"Проверено {summary.get('checked_dates', 0)} сочетаний направления и даты "
            f"в периоде {date.fromisoformat(summary['window_start']):%d.%m.%Y}–"
            f"{date.fromisoformat(summary['window_end']):%d.%m.%Y}. "
            "Это выбор из рассчитанных вариантов, а не минимум среди всех возможных дат."
        )
    lines.append("Сумма включает оба перелёта, багаж, отель и трансфер по прибытии.")
    if any(option.get("activity_reference") for option in plan.options):
        lines.append("Активности показаны по справке о направлениях. Занятия, аренда и дорога к ним не оценены.")
    lines.append("Данные демонстрационные. Обратный трансфер, визы и другие обязательные доплаты не оценены.")
    lines.append("Выберите поездку или измените условия.")
    return "\n\n".join(lines)


def package_price(option: dict[str, Any]) -> str:
    if option["total_rub"] is None:
        return f"цена неполная; известные расходы {rub(option['known_total_rub'])}"
    return f"{rub(option['total_rub'])} за показанные компоненты для всей группы"


def flight_source_line(label: str, leg: dict[str, Any]) -> str | None:
    demo = leg.get("demo_source") or {}
    if demo.get("kind") == "deterministic_mock":
        return f"{label}: демонстрационные расписание и цена; наличие мест не подтверждено."
    source = leg.get("price_source")
    if source:
        snapshot = f", запись {source['recorded_at'][:10]}" if source.get("recorded_at") else ""
        return (
            f"{label}: сохранённая котировка {rub(source['seat_price_rub'])} за одно место{snapshot}; "
            "наличие не подтверждено."
        )
    return None


def packages(plan: PlanResult, diff: str = "") -> str:
    if not plan.options:
        return " ".join(plan.notes) + " Попробуйте другие даты или направление."
    lines = [diff] if diff else []
    lines.append("Известные расходы: перелёты туда и обратно с багажом, отель и трансфер из аэропорта.")
    for option in plan.options:
        lines.append(f"{option['index']}. {option['label']}: {option['hotel']['name']} — {package_price(option)}.")
        why = "; ".join(option.get("why", [])[:1])
        timeline = option.get("timeline") or {}
        arrival = (
            f" Приезд в отель {when(timeline['hotel_arrival_at'])}; местное время."
            if timeline.get("hotel_arrival_at")
            else ""
        )
        lines.append(
            f"   {plural(option['hotel']['nights'], 'ночь', 'ночи', 'ночей')} с {day(option['hotel']['check_in'])}; "
            + flight_line(option)
            + arrival
            + (" " + why + "." if why else "")
        )
        limitations = [issue["message"] for issue in option.get("issues", []) if issue.get("severity") == "critical"]
        travelers = option.get("travelers") or {}
        if travelers.get("pets") and option["hotel"].get("pets_allowed") is not True:
            limitations.append("Отель не подтвердил размещение с питомцем — нужно уточнить до оплаты")
        if travelers.get("children_ages") and option["hotel"].get("adults_only") is not False:
            limitations.append("неизвестно, принимает ли отель детей — нужно уточнить до оплаты")
        limitations += [issue["message"] for issue in option.get("issues", []) if issue.get("severity") == "warning"]
        limitations += option.get("tradeoffs", []) + option.get("risks", [])
        limitations = list(dict.fromkeys(limitations))[:2]
        lines.append(
            "   Ограничения: "
            + ("; ".join(limitations) if limitations else "наличие и окончательная цена покупки не подтверждены")
            + "."
        )
    lines.append("Обратный трансфер, виза и прочие обязательные доплаты не оценены.")
    if any(option[leg].get("price_source") for option in plan.options for leg in ("outbound", "inbound")):
        lines.append("В перелётах есть сохранённые котировки; цена группы, багаж, отель и трансфер — расчётные.")
    if any(option.get("is_synthetic") for option in plan.options):
        lines.append("Данные демонстрационные; наличие и окончательная цена покупки не подтверждены.")
    else:
        lines.append("Наличие и окончательная цена покупки не подтверждены.")
    assumption = assumptions_line(plan.assumptions)
    if assumption:
        lines.append(assumption)
    lines.append("Выберите вариант или измените условия поездки.")
    return "\n".join(lines)


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
        + (
            ", возвратный тариф; сроки и удержания уточняются"
            if o["hotel"]["refundable"] is True
            else ", без возврата"
            if o["hotel"]["refundable"] is False
            else ", условия возврата неизвестны"
        ),
    ]
    if o["transfer"]:
        t = o["transfer"]
        lines.append(f"• Трансфер: {hours(t['duration_minutes'])} — {rub(t['price_rub'])}")
    lines += [
        f"• Обратно: {o['inbound']['from']} → {o['inbound']['to']}, {when(o['inbound']['departure_at'])}"
        f" — {rub(o['inbound']['price_rub'])}",
        f"Стоимость показанных компонентов ({group(brief)}): {package_price(o)}.",
    ]
    baggage = o["outbound"].get("baggage_price_rub", 0) + o["inbound"].get("baggage_price_rub", 0)
    lines.append(f"Расчётная доплата за багаж включена: {rub(baggage)}.")
    for label, leg in (("Туда", "outbound"), ("Обратно", "inbound")):
        source_line = flight_source_line(label, o[leg])
        if source_line:
            lines.append(source_line)
    lines.append("Обратный трансфер, виза и прочие обязательные доплаты не оценены.")
    critical = [i["message"] for i in o["issues"] if i["severity"] == "critical"]
    if critical:
        lines.append("Внимание: " + "; ".join(critical) + ".")
    lines.append("Оформляем? Это имитация: деньги не списываются.")
    return "\n".join(lines)


def booked(booking: dict[str, Any]) -> str:
    return (
        f"Готово! Демо-бронь {booking['id']}: {booking['hotel']}, "
        f"показанные компоненты {rub(booking['total_rub'])}. "
        "Это имитация оплаты, реального бронирования не было."
    )


def cancelled() -> str:
    return "Хорошо, не оформляю. Можно выбрать другой вариант или поменять условия."


def checkout_cancelled() -> str:
    return (
        "Остановил незавершённое оформление и снял выбор поездки. "
        "Демо-оплата не завершена; реальные бронирование, списание и возврат денег не выполнялись. "
        "Можно выбрать другой вариант или поменять условия."
    )


def unknown_destination(name: str) -> str:
    return (
        f"Не смог распознать направление «{name}» для нового подбора. Уточните название или IATA-код. "
        "В демо полный подбор перелёта, отеля и трансфера доступен для Антальи, Дубая и Мальдив; "
        "справочник направлений шире. Поезда доступны между Москвой и Петербургом."
    )


def booking_status(booking: dict[str, Any] | None) -> str:
    status = (booking or {}).get("status")
    if status == "paid_demo":
        state = "Демо-заказ отмечен как оплаченный; заказ и документы сохранены. "
    elif status in {"awaiting_travelers", "ready_for_demo_payment", "payment_declined"}:
        state = "Демо-оплата не завершена; сохранена только корзина поездки. "
    elif booking:
        state = "Создана демонстрационная бронь; реальное бронирование не выполнялось. "
    else:
        state = "Реальное бронирование не выполнялось. "
    return state


def cancellation_status(booking: dict[str, Any] | None, option: dict[str, Any] | None = None) -> str:
    """Explain the observed state; this chat never cancels an order or grants a refund."""
    refundable = (option or {}).get("hotel", {}).get("refundable")
    conditions = (
        "Тариф отеля возвратный, но сроки, удержания и бесплатная отмена не подтверждены. "
        if refundable is True
        else "Тариф отеля невозвратный. "
        if refundable is False
        else "Условия возврата нужно уточнить у отеля до оформления. "
    )
    return (
        booking_status(booking) + conditions + "Отмена и возврат не выполнялись. В демо реальные деньги не списываются."
    )


def off_topic() -> str:
    return "Я помогаю спланировать поездку. Расскажите, куда и когда хотите поехать — или что вам важно в отдыхе."


def trains(plan: PlanResult) -> str:
    classes = {"coupe": "купе", "platzkart": "плацкарт", "sv": "СВ", "seated": "сидячий"}
    lines = ["Поезда: расписание и расчётная стоимость за всю группу в одну сторону."] if plan.options else []
    for row in plan.options:
        source = row.get("schedule_source")
        if source == "synthetic":
            schedule = "Демонстрационное расписание"
        elif source == "yandex":
            schedule = (
                "Сохранённое расписание Яндекс Расписаний; актуальность не подтверждена"
                if row.get("schedule_cache_mode") == "replay"
                else "Расписание Яндекс Расписаний; актуальность не подтверждена"
            )
        else:
            schedule = "Источник расписания не указан"
        lines.append(
            f"{row['index']}. {row['origin']['name']} → {row['destination']['name']}, поезд {row['number']}, "
            f"{classes[row['seat_class']]}: {when(row['departure_at'])} — {when(row['arrival_at'])}, "
            f"{rub(row['price_rub'])}. {schedule}."
        )
    lines += plan.notes
    lines.append("Можно уточнить дату или класс мест. Оформление железнодорожных билетов пока не подключено.")
    return "\n".join(lines)
