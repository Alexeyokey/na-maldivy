"""Бриф поездки: что агент знает о запросе и откуда он это знает.

Временная схема до R1 (#4). Когда TripState из R1 появится в main, бриф станет его частью:
поля и их источники переносятся один к одному, а функции этого модуля останутся как есть.

Три правила:

1. Бриф меняет только код. Модель и правила из hidden.py предлагают ``BriefPatch``,
   а ``apply_patch`` проверяет каждое поле отдельно: неверный бюджет отклоняется,
   остальные изменения применяются.
2. У каждого заполненного поля есть источник: ``explicit`` — пользователь сказал прямо,
   ``inferred`` — агент вывел («с женой» → двое взрослых), ``default`` — агент допустил сам
   («на неделю», если срок не назван). Интерфейс (U2) показывает источники плашками,
   а пользователь правит допущения.
3. Поля, которые меняют результаты поиска, перечислены в ``SEARCH_FIELDS``. Их изменение
   помечает прежние варианты устаревшими, и агент пересобирает план, а не начинает заново.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.agent.places import to_iata
from app.contracts.tools import Travelers

Source = Literal["explicit", "inferred", "default"]

# Человекочитаемые названия — для вопросов, плашек и сообщений об отклонённых значениях.
FIELD_LABELS: dict[str, str] = {
    "transport": "вид транспорта",
    "seat_class": "класс мест в поезде",
    "origin": "город отправления",
    "destination": "направление",
    "month": "месяц поездки",
    "start_date": "дата отправления",
    "nights": "число ночей",
    "adults": "взрослые",
    "children_ages": "дети",
    "pets": "питомцы",
    "budget_rub": "бюджет",
    "max_flight_minutes": "максимум в пути",
    "direct_only": "только прямые рейсы",
    "refundable_only": "только возвратные тарифы",
    "min_stars": "звёзды отеля",
    "min_water_temperature_c": "температура воды",
    "visa_free_only": "без визы",
    "needs": "особые требования",
    "mood": "настроение поездки",
}

SEARCH_FIELDS: frozenset[str] = frozenset(FIELD_LABELS) - {"mood"}


class TripBrief(BaseModel):
    """Требования к поездке. None — пока неизвестно."""

    model_config = ConfigDict(extra="forbid")

    transport: Literal["flight", "train"] = "flight"
    seat_class: Literal["platzkart", "coupe", "sv", "seated"] = "coupe"
    origin: str | None = None
    destination: str | None = None
    month: int | None = Field(default=None, ge=1, le=12)
    start_date: date | None = None
    nights: int | None = Field(default=None, ge=1, le=60)
    adults: int = Field(default=1, ge=1, le=12)
    children_ages: list[int] = Field(default_factory=list, max_length=8)
    pets: int = Field(default=0, ge=0, le=3)
    budget_rub: int | None = Field(default=None, gt=0)
    max_flight_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    direct_only: bool = False
    refundable_only: bool = False
    min_stars: int | None = Field(default=None, ge=1, le=5)
    min_water_temperature_c: float | None = Field(default=None, ge=10, le=35)
    visa_free_only: bool = False
    needs: list[str] = Field(default_factory=list)
    mood: str | None = Field(default=None, max_length=200)

    @field_validator("origin")
    @classmethod
    def _known_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = to_iata(value)
        if code is None:
            raise ValueError(f"не знаю такого города вылета: {value}. Пока летаем из Москвы и Петербурга")
        return code

    @field_validator("destination")
    @classmethod
    def _known_destination(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # D5 returns more destinations than the three routes in the flight seed.
        # Accept its IATA codes; actual coverage is checked by the search tool.
        code = to_iata(value) or (value.upper() if re.fullmatch(r"[A-Za-z]{3}", value) else None)
        if code is None:
            raise ValueError(f"направления «{value}» нет в демо-каталоге; выберите карточку или укажите IATA-код")
        return code

    @field_validator("children_ages")
    @classmethod
    def _child_ages(cls, values: list[int]) -> list[int]:
        if any(age < 0 or age > 17 for age in values):
            raise ValueError("возраст ребёнка должен быть от 0 до 17 лет")
        return values

    @model_validator(mode="after")
    def _date_sets_month(self) -> "TripBrief":
        # Конкретная дата задаёт месяц; противоречие отклоняем, а не чиним молча.
        if self.start_date is not None:
            if self.month is None:
                self.month = self.start_date.month
            elif self.month != self.start_date.month:
                raise ValueError(f"дата {self.start_date.isoformat()} не попадает в месяц {self.month}")
        return self

    @property
    def travelers(self) -> Travelers:
        return Travelers(adults=self.adults, children_ages=list(self.children_ages), pets=self.pets)

    @property
    def group_size(self) -> int:
        return self.adults + len(self.children_ages)


class BriefPatch(BaseModel):
    """Предлагаемые изменения брифа. None — «в этом сообщении об этом не говорили»."""

    model_config = ConfigDict(extra="ignore")

    transport: Literal["flight", "train"] | None = None
    seat_class: Literal["platzkart", "coupe", "sv", "seated"] | None = None
    origin: str | None = None
    destination: str | None = None
    month: int | None = None
    start_date: str | None = Field(default=None, description="YYYY-MM-DD")
    nights: int | None = None
    adults: int | None = None
    children_ages: list[int] | None = None
    pets: int | None = None
    budget_rub: int | None = None
    max_flight_minutes: int | None = None
    direct_only: bool | None = None
    refundable_only: bool | None = None
    min_stars: int | None = None
    min_water_temperature_c: float | None = None
    visa_free_only: bool | None = None
    needs_add: list[str] = Field(default_factory=list)
    needs_remove: list[str] = Field(default_factory=list)
    mood: str | None = None
    # Явно снятые ограничения: «бюджет не важен» → ["budget_rub"].
    cleared: list[str] = Field(default_factory=list)
    # Поля, которые выведены, а не сказаны прямо. Остальные заполненные поля — explicit.
    inferred: list[str] = Field(default_factory=list)

    def values(self) -> dict[str, Any]:
        """Заполненные поля брифа, без служебных списков."""
        service = {"needs_add", "needs_remove", "cleared", "inferred"}
        return {k: v for k, v in self.model_dump(exclude_none=True).items() if k not in service}

    def is_empty(self) -> bool:
        return not (self.values() or self.needs_add or self.needs_remove or self.cleared)


@dataclass
class FieldChange:
    field: str
    old: Any
    new: Any
    source: Source | None


@dataclass
class RejectedChange:
    field: str
    value: Any
    reason: str


@dataclass
class ApplyResult:
    brief: TripBrief
    sources: dict[str, Source]
    changes: list[FieldChange] = field(default_factory=list)
    rejected: list[RejectedChange] = field(default_factory=list)

    @property
    def search_changed(self) -> bool:
        return any(c.field in SEARCH_FIELDS for c in self.changes)


def merge_patches(primary: BriefPatch, secondary: BriefPatch) -> BriefPatch:
    """Значения primary важнее; secondary дополняет пропуски и добавляет свои требования."""
    data = secondary.model_dump()
    for name, value in primary.model_dump(exclude_none=True).items():
        if name in ("needs_add", "needs_remove", "cleared", "inferred"):
            data[name] = list(dict.fromkeys([*data[name], *value]))
        else:
            data[name] = value
    # Поле, которое primary задал явно, не должно остаться выведенным из secondary.
    explicit_primary = set(primary.values()) - set(primary.inferred)
    data["inferred"] = [n for n in data["inferred"] if n not in explicit_primary]
    return BriefPatch.model_validate(data)


def _short_error(exc: ValidationError) -> str:
    return "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())


def apply_patch(
    brief: TripBrief,
    sources: dict[str, Source],
    patch: BriefPatch,
    *,
    today: date,
    default_fields: frozenset[str] = frozenset(),
) -> ApplyResult:
    """Проверяет и применяет патч. Исходный бриф не меняется."""
    current = brief.model_dump()
    candidate = dict(current)
    rejected: list[RejectedChange] = []
    defaults = TripBrief().model_dump()

    for name in patch.cleared:
        if name in defaults:
            candidate[name] = defaults[name]

    values = patch.values()
    # Новая дата без месяца: месяц берём из даты, иначе старый месяц с ней конфликтует.
    if "start_date" in values:
        try:
            new_date = date.fromisoformat(values["start_date"])
        except ValueError:
            rejected.append(RejectedChange("start_date", values.pop("start_date"), "дата не в формате ГГГГ-ММ-ДД"))
        else:
            if new_date <= today:
                rejected.append(RejectedChange("start_date", values.pop("start_date"), "дата уже прошла"))
            else:
                values.setdefault("month", new_date.month)
    if "month" in values and "start_date" not in values and candidate["start_date"] is not None:
        if candidate["start_date"].month != values["month"]:
            candidate["start_date"] = None  # «а если в декабре?» — старая дата больше не действует

    for name, value in values.items():
        trial = {**candidate, name: value}
        try:
            checked = TripBrief.model_validate(trial)
        except ValidationError as exc:
            rejected.append(RejectedChange(name, value, _short_error(exc)))
            continue
        # Валидатор мог нормализовать значение («Питер» → LED): храним нормализованное.
        candidate = checked.model_dump()

    if patch.needs_add or patch.needs_remove:
        removed = set(patch.needs_remove)
        needs = [n for n in candidate["needs"] if n not in removed]
        needs += [n for n in patch.needs_add if n not in removed and n not in needs]
        candidate["needs"] = needs

    new_brief = TripBrief.model_validate(candidate)
    new_dump = new_brief.model_dump()
    new_sources = dict(sources)
    changes: list[FieldChange] = []
    for name, new_value in new_dump.items():
        if new_value == current[name]:
            continue
        if name in patch.cleared and new_value == defaults[name]:
            new_sources.pop(name, None)
            source: Source | None = None
        elif name in default_fields:
            source = "default"
        elif (
            name in patch.inferred
            or name == "needs"  # скрытые требования всегда выведены, а не названы полем
            or (name == "month" and "start_date" in values and "month" not in patch.values())
        ):
            source = "inferred"
        else:
            source = "explicit"
        if source:
            new_sources[name] = source
        changes.append(FieldChange(name, current[name], new_value, source))

    return ApplyResult(brief=new_brief, sources=new_sources, changes=changes, rejected=rejected)
