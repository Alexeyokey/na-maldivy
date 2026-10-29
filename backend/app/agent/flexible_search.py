"""Bounded comparisons of complete packages over an explicitly flexible period.

Calendar prices only nominate dates. Ranking uses the actual two flights,
baggage, hotel, arrival transfer and validator output from the configured tools.
No claim is made about unexamined dates or supplier availability.
"""

import calendar
import copy
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from yaml import YAMLError

from app.agent.brief import TripBrief
from app.agent.destination_policy import DestinationPolicy, check_destination_policy
from app.agent.places import place_name
from app.agent.planner import DEFAULT_NIGHTS, Assumption, PlanResult, ToolCaller, search_trip
from app.tools.destinations import Catalogue, load_catalogue

MAX_DESTINATIONS = 6
DATES_PER_MONTH = 3
MAX_MONTHS = 12
ACTIVITY_NEEDS = frozenset({"active", "surfing", "diving"})
OFFICIAL_ACTIVITY_HOSTS = frozenset(
    {"www.srilanka.travel", "www.indonesia.travel", "thailand.go.th", "www.phuket.go.th"}
)


def _catalogue(policy: DestinationPolicy | None) -> Catalogue | None:
    if policy is None:
        return None
    try:
        return policy.catalogue or (load_catalogue(policy.data_path) if policy.data_path else None)
    except (OSError, ValueError, TypeError, YAMLError):
        return None


def _activities(policy: DestinationPolicy | None) -> dict[str, dict[str, Any]]:
    # Only the explicitly configured data tree supplies these facts.
    if policy is None or policy.data_path is None:
        return {}
    try:
        raw = json.loads((Path(policy.data_path).parent / "activity-references.json").read_text())
        if raw.get("schema_version") != 1 or raw.get("scope") != "destination_only":
            return {}
        result = {}
        for code, row in raw["destinations"].items():
            activities = row["activities"]
            sources = row["sources"]
            if (
                not isinstance(code, str)
                or len(code) != 3
                or not activities
                or not set(activities).issubset({"surfing", "diving"})
                or not sources
            ):
                return {}
            for source in sources:
                url = urlsplit(source["url"])
                date.fromisoformat(source["checked_on"])
                if url.scheme != "https" or url.hostname not in OFFICIAL_ACTIVITY_HOSTS or url.username:
                    return {}
            result[code] = {"activities": list(activities), "sources": copy.deepcopy(sources)}
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def _months(brief: TripBrief, today: date) -> list[tuple[str, date, date]]:
    named = sorted(date.fromisoformat(month + "-01") for month in brief.departure_months)
    requested_start = brief.departure_window_start or (named[0] if named else today + timedelta(days=1))
    start = max(requested_start, today + timedelta(days=1))
    last_named = named[-1] if named else start
    end = brief.departure_window_end or last_named.replace(
        day=calendar.monthrange(last_named.year, last_named.month)[1]
    )
    cursor = start.replace(day=1)
    result = []
    while cursor <= end:
        last = cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
        key = cursor.strftime("%Y-%m")
        if not brief.departure_months or key in brief.departure_months:
            result.append((key, max(start, cursor), min(end, last)))
        if cursor.year == 9999 and cursor.month == 12:
            break
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return result


async def search_flexible_trip(
    brief: TripBrief,
    call: ToolCaller,
    today: date,
    *,
    supported_destinations: frozenset[str],
    destination_policy: DestinationPolicy | None,
) -> PlanResult:
    result = PlanResult()
    assert brief.origin and brief.has_flexible_dates
    months = _months(brief, today)
    if not months or len(months) > MAX_MONTHS:
        result.notes.append("Укажите будущий период поиска продолжительностью не более года.")
        return result
    nights = brief.nights or DEFAULT_NIGHTS
    if brief.nights is None:
        result.assumptions.append(Assumption("nights", "считаю 7 ночей; длительность можно изменить"))
    requested = set(brief.needs) & ACTIVITY_NEEDS
    activity_references = _activities(destination_policy)
    catalogue = _catalogue(destination_policy)
    records = {row.iata: row for row in catalogue.destinations} if catalogue else {}
    if brief.destination:
        candidates = [brief.destination] if brief.destination in supported_destinations else []
    else:
        if catalogue is None:
            result.notes.append(
                "Справочник направлений недоступен. Укажите конкретное направление или повторите поиск."
            )
            return result
        candidates = sorted(set(records) & supported_destinations)
        if "sea" in brief.needs:
            candidates = [code for code in candidates if "beach" in records[code].tags]
        if requested:
            sports = requested & {"surfing", "diving"}
            sourced = [
                code
                for code in candidates
                if code in activity_references and sports.issubset(activity_references[code]["activities"])
            ]
            # An absent reference never becomes a claim about an activity.
            if sourced:
                candidates = sourced
        candidates = candidates[:MAX_DESTINATIONS]
    summary = {
        "kind": "flexible_dates",
        "window_start": months[0][1].isoformat(),
        "window_end": months[-1][2].isoformat(),
        "checked_dates": 0,
        "checked_destinations": 0,
        "complete_quotes": 0,
        "within_budget": 0,
        "scope": "sampled_dates",
    }
    result.search_summary = summary
    cache: dict[str, Any] = {}
    failures = set()

    async def cached_call(name: str, arguments: dict[str, Any]) -> Any:
        key = json.dumps([name, arguments], ensure_ascii=False, sort_keys=True)
        if key not in cache:
            cache[key] = await call(name, arguments)
            if cache[key] is None:
                failures.add(name)
        return cache[key]

    found: list[dict[str, Any]] = []
    checked_destinations = set()
    for code in candidates:
        for month, first, last in months:
            monthly = brief.model_copy(update={"destination": code, "start_date": None})
            days = await cached_call(
                "price_calendar",
                {
                    "origin": brief.origin,
                    "destination": code,
                    "month": month,
                    "travelers": brief.travelers.model_dump(),
                },
            )
            if days is None:
                continue
            eligible = [
                row
                for row in days
                if first <= row.date <= last
                and brief.accepts_departure(row.date)
                and not check_destination_policy(
                    destination_policy, monthly, row.date, row.date + timedelta(days=nights)
                )
            ]
            selected = sorted(eligible, key=lambda row: (row.price_rub, row.date))[:DATES_PER_MONTH]
            for row in selected:
                exact = monthly.model_copy(
                    update={"start_date": row.date, "month": row.date.month, "year": row.date.year}
                )
                summary["checked_dates"] += 1
                checked_destinations.add(code)
                plan = await search_trip(exact, cached_call, today, destination_policy=destination_policy)
                for option in plan.options:
                    if not option.get("can_confirm") or type(option.get("total_rub")) is not int:
                        continue
                    summary["complete_quotes"] += 1
                    if brief.budget_rub is not None and option["known_total_rub"] > brief.budget_rub:
                        continue
                    summary["within_budget"] += 1
                    option = copy.deepcopy(option)
                    if code in records:
                        water = records[code].climate.months[row.date.month - 1].water_temp_c
                        option["destination_reference"] = {
                            "name": records[code].name,
                            "water_temperature_c": water,
                            "scope": "monthly_climate_normal",
                            "as_of": catalogue.as_of.isoformat(),
                        }
                        if water is not None and "sea" in brief.needs:
                            option["why"].insert(0, f"Месячная норма воды около {water:.0f} °C; это не прогноз")
                    if requested:
                        reference = activity_references.get(code)
                        option["activity_reference"] = {
                            "requested": sorted(requested),
                            "destination_activities": reference["activities"] if reference else [],
                            "sources": reference["sources"] if reference else [],
                            "scope": "destination_only",
                            "hotel_availability_confirmed": False,
                            "included_in_price": False,
                        }
                        labels = {"surfing": "сёрфинг", "diving": "дайвинг"}
                        described = (
                            ", ".join(labels[activity] for activity in reference["activities"]) if reference else ""
                        )
                        activity_note = (
                            f"В справке о направлении: {described}; занятия и аренда оплачиваются отдельно"
                            if described
                            else "Возможность занятий активным отдыхом нужно уточнить; занятия и аренда не оценены"
                        )
                        option["why"].insert(0, activity_note)
                        option["risks"].append("Занятия, инструктор, аренда и дорога до места активности не оценены.")
                        option["unpriced_components"].append("activities_and_equipment")
                    found.append(option)
    summary["checked_destinations"] = len(checked_destinations)
    # Prefer different destinations, then fill with different hotels if fewer
    # destinations have a complete compatible quote within the requested budget.
    ranked = sorted(
        found, key=lambda row: (row["known_total_rub"], row["outbound"]["departure_at"], row["hotel"]["hotel_id"])
    )
    seen_destinations, seen_hotels = set(), set()
    picks = []
    for distinct_destination in (True, False):
        for option in ranked:
            if option["hotel"]["hotel_id"] in seen_hotels:
                continue
            if distinct_destination and option["destination"] in seen_destinations:
                continue
            picks.append(option)
            seen_destinations.add(option["destination"])
            seen_hotels.add(option["hotel"]["hotel_id"])
            if len(picks) == 3:
                break
        if len(picks) == 3:
            break
    result.options = sorted(picks, key=lambda row: row["known_total_rub"])
    for index, option in enumerate(result.options, 1):
        option["index"] = index
        option["label"] = place_name(option["destination"])
    result.notes.append(
        f"Проверено {summary['checked_dates']} сочетаний направления и даты в "
        f"{len(months)} месяцах для {summary['checked_destinations']} направлений. "
        "Сравнены известные расходы полной сборки: оба перелёта, багаж, отель и трансфер по прибытии. "
        "Показаны лучшие рассчитанные варианты разных направлений; все даты и все направления не перебирались."
    )
    if failures:
        result.notes.append("Часть источников не ответила; показаны только успешно рассчитанные варианты.")
    if not result.options:
        result.notes.append(
            "Среди проверенных дат не нашлось совместимой поездки в заданном бюджете. Условия сохранены."
        )
    if requested:
        result.notes.append(
            "Занятия, аренда снаряжения и дорога к ним не включены в сумму; наличие у отеля не проверено."
        )
    return result
