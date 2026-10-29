"""Sourced destination advisories, including periods beyond a stored visa rule.

The catalogue is a dated snapshot. This module never turns it into fresh legal
verification, confirmed inventory, or an unknown price represented as zero.
"""

import calendar
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.agent.brief import TripBrief
from app.agent.coverage import full_trip_destinations
from app.tools.destinations import load_catalogue


@dataclass(frozen=True)
class DestinationInformation:
    text: str
    facts: dict[str, Any]


def is_destination_information_question(text: str) -> bool:
    """Recognise factual visa/total-price questions without a request-specific rule."""
    normalized = text.casefold().replace("ё", "е")
    return bool(
        re.fullmatch(r"\s*расскажи\s+о\s+направлении\s+[a-z]{3}[.!?]?\s*", normalized)
        or ("?" in normalized and re.search(r"виз|въезд|паспорт|точно\s+можно", normalized))
        or re.search(r"(?:сколько|какова)\s+(?:всего|полная\s+стоимость)", normalized)
    )


def destination_information(
    brief: TripBrief, tools: Mapping[str, Any], *, today: date
) -> DestinationInformation | None:
    """Return stored source facts even when origin or a supported flight is absent.

    A month-only request checks its full possible departure window, not an
    invented departure day. A year inferred from today's date is labelled and
    cannot establish the visa period for an explicitly undated trip.
    """
    tool = tools.get("suggest_destinations")
    if tool is None or not brief.destination:
        return None
    catalogue = getattr(tool, "catalogue", None)
    data_dir = getattr(tool, "data_dir", None)
    if catalogue is None:
        if data_dir is None:
            return None
        catalogue = load_catalogue(data_dir / "destinations.yaml")
    record = next((item for item in catalogue.destinations if item.iata == brief.destination), None)
    if record is None:
        return None
    visa = record.visa
    supported = record.iata in full_trip_destinations(tools)
    year = getattr(brief, "year", None)
    nights = brief.nights
    start, end = None, None
    period_kind = "unknown"
    if brief.start_date is not None:
        start = brief.start_date
        period_kind = "exact_departure"
        if nights is not None:
            try:
                end = start + timedelta(days=nights)
            except OverflowError:
                pass
    elif brief.month and year:
        start = date(year, brief.month, 1)
        period_kind = "whole_month_departure_window"
        if nights is not None:
            last = date(year, brief.month, calendar.monthrange(year, brief.month)[1])
            try:
                end = last + timedelta(days=nights)
            except OverflowError:
                pass
    period_valid = None
    reasons = []
    if start is not None:
        if visa.effective_from is not None and start < visa.effective_from:
            period_valid = False
            reasons.append("before_effective_from")
        if visa.effective_until is not None and (end or start) > visa.effective_until:
            period_valid = False
            reasons.append("after_effective_until")
        if (
            period_valid is not False
            and end is not None
            and visa.regime != "unknown"
            and visa.verification_status in {"official_verified", "domestic"}
        ):
            period_valid = True
    stay_days = nights + 1 if nights is not None else None
    stay_valid = stay_days <= visa.max_stay_days if stay_days is not None and visa.max_stay_days is not None else None
    if stay_valid is False:
        reasons.append("stay_exceeds_limit")
    source = catalogue.sources.get(visa.source) if visa.source else None
    urls = [str(source.url)] if source is not None else []
    prices = record.prices.model_dump(mode="json")
    facts = {
        "destination": record.iata,
        "name": record.name,
        "as_of": catalogue.as_of.isoformat(),
        "checked_against_date": today.isoformat(),
        "visa": visa.model_dump(mode="json"),
        "source_urls": urls,
        "source_retrieved_at": source.retrieved_at.isoformat() if source is not None else None,
        "requested_year": year,
        "requested_month": brief.month,
        "requested_period_kind": period_kind,
        "trip_start": start.isoformat() if start else None,
        "trip_end": end.isoformat() if end else None,
        "visa_period_valid": period_valid,
        "stay_days": stay_days,
        "stay_limit_valid": stay_valid,
        "limitations": reasons,
        "prices": prices,
        "full_trip_price_rub": None,
        "availability_confirmed": False,
        "live_verified": False,
        "full_trip_supported": supported,
    }
    text = [
        f"{record.name}: сохранённая визовая справка для обычного паспорта РФ, туризм; "
        f"срез {catalogue.as_of:%d.%m.%Y}, проверка источника "
        f"{visa.checked_on:%d.%m.%Y}."
        if visa.checked_on
        else f"{record.name}: сохранённая справка; дата проверки визового источника неизвестна.",
        f"Условия: {visa.conditions}",
    ]
    if not supported:
        text.insert(
            0,
            f"{record.name} ({record.iata}) — справочное направление. "
            "Подбор полной поездки с отелем и трансфером для него пока недоступен. "
            "Для полного подбора можно выбрать Анталью, Дубай или Мальдивы.",
        )
    if brief.month:
        climate = record.climate.months[brief.month - 1]
        climate_parts = []
        if climate.air_temp_c is not None:
            climate_parts.append(f"воздух {climate.air_temp_c:+g} °C")
        if climate.water_temp_c is not None:
            climate_parts.append(f"вода {climate.water_temp_c:+g} °C")
        if climate_parts:
            text.insert(
                1,
                f"Климатическая справка для месяца {brief.month}: "
                + "; ".join(climate_parts)
                + ". Это среднемноголетние значения, не прогноз погоды.",
            )
    regime = {
        "exempt": "безвизовый режим",
        "domestic": "внутренняя поездка",
        "on_arrival": "виза по прибытии",
        "evisa": "электронная виза",
        "required": "требуется виза",
        "unknown": "режим неизвестен",
    }[visa.regime]
    text.append(f"В сохранённом источнике: {regime}; статус проверки — {visa.verification_status}.")
    if visa.effective_until:
        text.append(f"Указанный срок действия правила — до {visa.effective_until:%d.%m.%Y}.")
    if period_kind == "whole_month_departure_window":
        text.append("Точный день не задан: проверено всё окно вылета в указанном месяце с учётом срока поездки.")
    if period_valid is False:
        text.append(
            "Указанный период поездки выходит за срок действия сохранённого правила. "
            "Безвизовый въезд на эти даты не подтверждён; гарантировать его нельзя. "
            "Нужно перепроверить действующие правила у официального источника."
        )
    elif period_valid is None:
        text.append("Дат недостаточно для проверки всего периода поездки; условия въезда не подтверждены.")
    else:
        text.append(
            "Период укладывается в сохранённый срок правила. Это не гарантия въезда: "
            "актуальные правила и документы нужно перепроверить перед поездкой."
        )
    if stay_valid is False:
        text.append(f"Пребывание {stay_days} дней превышает сохранённый лимит {visa.max_stay_days} дней.")
    elif visa.max_stay_days:
        text.append(f"Сохранённый предел пребывания — {visa.max_stay_days} дней; предыдущие поездки не проверены.")
    text.append(
        "Полная стоимость неизвестна: справочник не подтверждает цены перелёта, проживания и всех доплат. "
        "Неизвестная цена не означает бесплатную поездку; наличие мест не проверено."
    )
    if urls:
        text.append("Источник сохранённой справки: " + ", ".join(urls))
    return DestinationInformation("\n".join(text), facts)
