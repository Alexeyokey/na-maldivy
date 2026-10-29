"""Sourced D5 hard filters shared by package search and date comparisons.

This checks a checked-in reference snapshot, not current weather or a fresh
immigration guarantee. An absent configured source never falls back to another
catalogue or to invented legal/climate facts.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from yaml import YAMLError

from app.agent.brief import TripBrief
from app.tools.destinations import Catalogue, load_catalogue


def has_destination_filters(brief: TripBrief) -> bool:
    return brief.min_water_temperature_c is not None or brief.visa_free_only


@dataclass(frozen=True)
class DestinationPolicy:
    catalogue: Catalogue | None = None
    data_path: Path | None = None

    def check(self, brief: TripBrief, departure: date, return_date: date) -> list[str]:
        if not has_destination_filters(brief):
            return []
        if return_date < departure:
            return ["Период поездки некорректен; климатические и визовые ограничения не проверены"]
        try:
            catalogue = self.catalogue or (load_catalogue(self.data_path) if self.data_path is not None else None)
        except (OSError, ValueError, TypeError, YAMLError):
            return ["Источник климатической и визовой справки недоступен; обязательные условия не подтверждены"]
        if catalogue is None:
            return ["Нет настроенного источника климатической и визовой справки; обязательные условия не подтверждены"]
        record = next((row for row in catalogue.destinations if row.iata == brief.destination), None)
        if record is None:
            return ["Направление отсутствует в настроенной справке; климатические и визовые условия не подтверждены"]
        notes = []
        snapshot = f"по сохранённой справке от {catalogue.as_of:%d.%m.%Y}"
        if brief.min_water_temperature_c is not None:
            water = record.climate.months[departure.month - 1].water_temp_c
            if water is None or record.climate.water_source is None:
                notes.append(
                    f"Температура воды в месяце вылета {departure.month:02d} неизвестна {snapshot}; "
                    f"требование не ниже {brief.min_water_temperature_c:g} °C не подтверждено. "
                    "Климатическая норма не является прогнозом."
                )
            elif water < brief.min_water_temperature_c:
                notes.append(
                    f"Климатическая норма воды в месяце вылета {departure.month:02d}: {water:g} °C {snapshot}; "
                    f"это ниже требования {brief.min_water_temperature_c:g} °C. Это норма, а не прогноз."
                )
        if brief.visa_free_only:
            visa = record.visa
            domestic = visa.regime == "domestic" and record.country_code == "RU"
            exempt = visa.regime == "exempt" and visa.verification_status == "official_verified"
            if not (domestic or exempt):
                notes.append(
                    f"Безвизовый въезд для туриста с обычным паспортом РФ не подтверждён {snapshot}; "
                    "электронная виза и виза по прибытии не выполняют условие «без визы»."
                )
            elif visa.checked_on is None or visa.source is None or visa.source not in catalogue.sources:
                notes.append(
                    f"У визового правила нет проверенной даты или источника {snapshot}; безвизовый въезд не подтверждён"
                )
            else:
                if (visa.effective_from is not None and departure < visa.effective_from) or (
                    visa.effective_until is not None and return_date > visa.effective_until
                ):
                    notes.append(
                        f"Сохранённое визовое правило не покрывает всю поездку {departure:%d.%m.%Y}–"
                        f"{return_date:%d.%m.%Y} {snapshot}; безвизовый въезд на этот период не подтверждён."
                    )
                days = (return_date - departure).days + 1
                if not domestic and visa.max_stay_days is None:
                    notes.append(f"Допустимый срок безвизового пребывания неизвестен {snapshot}")
                elif visa.max_stay_days is not None and days > visa.max_stay_days:
                    notes.append(
                        f"Период поездки с днями вылета и возвращения — {days} дней; он превышает "
                        f"сохранённый лимит {visa.max_stay_days} дней {snapshot}."
                    )
        return notes


def destination_policy_for_tools(tools: Mapping[str, Any]) -> DestinationPolicy | None:
    """Use only the D5 tool's explicit source; stub DTOs are not legal evidence."""
    tool = tools.get("suggest_destinations")
    if tool is None:
        return None
    catalogue = getattr(tool, "catalogue", None)
    if isinstance(catalogue, Catalogue):
        return DestinationPolicy(catalogue=catalogue)
    if catalogue is not None:
        return DestinationPolicy()  # An invalid explicitly injected source cannot select a fallback file.
    data_dir = getattr(tool, "data_dir", None)
    if data_dir is not None:
        return DestinationPolicy(data_path=Path(data_dir) / "destinations.yaml")
    return None


def check_destination_policy(
    policy: DestinationPolicy | None, brief: TripBrief, departure: date, return_date: date
) -> list[str]:
    if not has_destination_filters(brief):
        return []
    if policy is None:
        return ["Нет настроенной климатической и визовой справки; обязательные условия не подтверждены"]
    return policy.check(brief, departure, return_date)
