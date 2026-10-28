"""Словарь скрытых требований и разбор сообщения правилами (A4).

Пользователь пишет «с ребёнком», а подразумевает: семейный отель, детское кресло в трансфере,
перелёт покороче, и возраст ребёнка влияет на цену. Этот модуль хранит такие связи явно, чтобы
их можно было показать, проверить тестами и поправить без переписывания промпта.

Правила работают в двух ролях:

1. Дополняют разбор моделью. Модель может пропустить скрытое требование, правило — нет.
   Значения модели важнее, правила лишь заполняют пропуски (``merge_patches``).
2. Заменяют модель, когда она недоступна или работает фейковый провайдер. Тогда агент
   остаётся рабочим на типовых фразах, но качество разбора нужно мерить на реальной модели (A3).
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.agent.brief import BriefPatch, TripBrief
from app.agent.places import DESTINATIONS, ORIGINS, to_iata


@dataclass(frozen=True)
class Need:
    """Скрытое требование: как называется и что агент из-за него делает по-другому."""

    label: str
    effect: str


NEEDS: dict[str, Need] = {
    "child": Need("с ребёнком", "семейные отели без «только для взрослых», детское кресло в трансфере"),
    "pets": Need("с питомцем", "только отели, где разрешены животные, и трансфер с перевозкой животных"),
    "sea": Need("море и пляж", "направления с тёплой водой"),
    "warm": Need("тепло", "направления, где днём от +24 °C"),
    "budget_low": Need("недорого", "сначала дешёвые варианты, дорогие только с объяснением"),
    "short_flight": Need("короткий перелёт", "варианты с перелётом до 5 часов выше в списке"),
    "romantic": Need("романтическая поездка", "отели «только для взрослых» выше в списке"),
    "late_arrival": Need("поздний прилёт", "отели с круглосуточной стойкой регистрации"),
    "short_transfer": Need("короткий трансфер", "отели ближе к аэропорту"),
    "quiet": Need("спокойный отдых", "небольшие и «только для взрослых» отели выше в списке"),
    "flexible": Need("планы могут поменяться", "тарифы с бесплатной отменой"),
}


@dataclass(frozen=True)
class Rule:
    need: str
    pattern: str
    # Поля брифа, которые правило выводит (source = inferred).
    sets: dict[str, Any] = field(default_factory=dict)


RULES: tuple[Rule, ...] = (
    Rule("child", r"реб[её]н|\bдет(и|ей|ьми|ям|ск)|\bсын|\bдоч|малыш|школьник"),
    Rule("pets", r"собак|собачк|\bпс[аоу]\b|\bпсом|кошк|\bкот(ом|ик|енк)|питомц|животн", {"pets": 1}),
    Rule("sea", r"мор[еяю]|пляж|покупаться|искупаться", {"min_water_temperature_c": 24.0}),
    Rule("warm", r"тепл|т[её]пл|жар|погреться|солнц|лето зимой"),
    Rule("budget_low", r"недорог|бюджетн|подешевле|эконом|не дорого|без лишних трат"),
    Rule("short_flight", r"без долг\w* перел|недалеко|недолго лететь|лететь недолго|ближе"),
    Rule("romantic", r"медов\w* месяц|романтич|годовщин|свадеб|свадьб", {"adults": 2}),
    Rule("late_arrival", r"прил[её]т\w* ночью|поздно прил[её]т|ночн\w* рейс"),
    Rule("short_transfer", r"с бабушк|с дедушк|пожил|с родителями|без долг\w* трансфер"),
    Rule("quiet", r"\bтих|\bтиш|спокойн|без тусов|без вечерин|без аниматор"),
    Rule(
        "flexible",
        r"бесплатн\w* отмен|с возвратом|возвратн|планы могут|могут поменяться|не уверен\w* в дат",
        {"refundable_only": True},
    ),
)

_MONTHS = (
    ("январ", 1),
    ("феврал", 2),
    ("март", 3),
    ("апрел", 4),
    ("ма[йяе]", 5),
    ("июн", 6),
    ("июл", 7),
    ("август", 8),
    ("сентябр", 9),
    ("октябр", 10),
    ("ноябр", 11),
    ("декабр", 12),
)
_WORD_NUMBERS = {"одн": 1, "дв": 2, "тр": 3, "четыр": 4, "пят": 5, "шест": 6, "сем": 7, "восем": 8, "десят": 10}


def _number(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    for stem, value in _WORD_NUMBERS.items():
        if word.startswith(stem):
            return value
    return None


def _future(month: int, day: int, today: date) -> date | None:
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate > today:
            return candidate
    return None


def _money(amount: str, unit: str | None) -> int:
    value = float(amount.replace(" ", "").replace(",", "."))
    if unit and unit.startswith(("тыс", "т", "k", "к")):
        value *= 1_000
    elif unit and unit.startswith(("млн", "миллион")):
        value *= 1_000_000
    return int(value)


def hidden_only(patch: BriefPatch) -> BriefPatch:
    """Часть разбора правилами, которая добавляется к ответу модели: скрытые требования и выведенное из них.

    Явные значения (даты, города, бюджет) решает модель: правило не должно превращать вопрос
    «а 20 ноября есть рейсы?» в перенос даты.
    """
    implied = {name for rule in RULES if rule.need in patch.needs_add for name in rule.sets}
    values = {name: value for name, value in patch.values().items() if name in implied}
    return BriefPatch(**values, needs_add=patch.needs_add, inferred=sorted(implied & set(values)))


def detect_needs(text: str) -> list[str]:
    lowered = text.lower()
    return [rule.need for rule in RULES if re.search(rule.pattern, lowered)]


def relative(text: str, brief: TripBrief) -> dict[str, Any]:
    """Изменения относительно брифа: «на два дня позже», «на 3 дня дольше», «бюджет меньше на 50 тысяч»."""
    t = text.lower().replace("ё", "е")
    data: dict[str, Any] = {}
    shift_days, direction = 0, ""
    if m := re.search(r"на (\d+|[а-я]+) (?:дн|день|дня|дней|сут)\w* (позже|раньше)", t):
        shift_days, direction = _number(m.group(1)) or 0, m.group(2)
    elif m := re.search(r"на неделю (позже|раньше)", t):
        shift_days, direction = 7, m.group(1)
    if shift_days and brief.start_date:
        sign = 1 if direction == "позже" else -1
        data["start_date"] = (brief.start_date + timedelta(days=sign * shift_days)).isoformat()
    longer = re.search(r"на (\d+|[а-я]+) (?:дн|день|дня|дней|ноч)\w* (дольше|больше|длиннее|короче|меньше)", t)
    if longer and brief.nights and (n := _number(longer.group(1))):
        data["nights"] = max(1, brief.nights + (n if longer.group(2) in ("дольше", "больше", "длиннее") else -n))
    money = re.search(r"(?:бюджет\w*|дешевле|дороже)\s*(меньше|больше)?\s*на\s*(\d[\d\s]*)\s*(тыс\w*|к|k)?", t)
    if money and brief.budget_rub:
        amount = _money(money.group(2).strip(), money.group(3))
        less = money.group(1) == "меньше" or "дешевле" in money.group(0)
        data["budget_rub"] = max(1, brief.budget_rub + (-amount if less else amount))
    return data


def extract(text: str, today: date, brief: TripBrief | None = None) -> BriefPatch:
    """Разбирает типовые фразы. Не угадывает: чего нет в тексте, того нет и в патче.

    С брифом понимает и относительные изменения («на два дня позже»).
    """
    t = text.lower().replace("ё", "е").replace("\u00a0", " ")
    data: dict[str, Any] = relative(text, brief) if brief else {}
    inferred: list[str] = []

    def infer(name: str, value: Any) -> None:
        if name not in data:
            data[name] = value
            inferred.append(name)

    # Откуда и куда: «из Москвы», «в Анталью», «на Мальдивы».
    for match in re.finditer(r"\b(из|с|от)\s+([а-яa-z-]+)", t):
        code = to_iata(match.group(2))
        if code in ORIGINS:
            data["origin"] = code
            break
    for match in re.finditer(r"\b(в|во|на)\s+([а-яa-z-]+)", t):
        code = to_iata(match.group(2))
        if code in DESTINATIONS:
            data["destination"] = code
            break

    # Когда: «18 ноября», «в ноябре».
    for stem, month in _MONTHS:
        day_match = re.search(rf"\b(\d{{1,2}})\s+{stem}", t)
        if day_match and (start := _future(month, int(day_match.group(1)), today)):
            data["start_date"] = start.isoformat()
            break
        if re.search(rf"\b{stem}", t):
            data["month"] = month
            break

    # Срок: «на неделю», «на 5 ночей», «дней на десять».
    if "nights" in data:
        pass  # уже посчитано относительно брифа
    elif re.search(r"на (одну )?недел(ю|ьку)(?! (позже|раньше))", t):
        data["nights"] = 7
    elif re.search(r"на две недели|на 2 недели", t):
        data["nights"] = 14
    elif m := re.search(r"(\d+|[а-я]+)\s+ноч\w*\b(?!\s+(?:дольше|больше|короче|меньше))", t):
        if (n := _number(m.group(1))) is not None:
            data["nights"] = n
    elif m := re.search(r"(\d+|[а-я]+)\s+(?:дн|день|суток)\w*\b(?!\s+(?:позже|раньше|дольше|больше|короче|меньше))", t):
        if (n := _number(m.group(1))) is not None:
            data["nights"] = max(1, n - 1)
            inferred.append("nights")
    elif re.search(r"на выходные|уикенд", t):
        infer("nights", 2)

    # Бюджет: «до 150 000 ₽», «бюджет 200к», «тысяч за 100».
    money = re.search(
        r"(?:бюджет\w*|до|за|не дороже|в пределах)\s*(\d[\d\s]*[.,]?\d*)\s*(тыс\w*|т\.?р|k|к|млн|миллион\w*)?",
        t,
    )
    if money and money.group(1).strip() and "budget_rub" not in data:
        amount = _money(money.group(1).strip(), money.group(2))
        if amount >= 5_000:  # «до 5 часов» — не бюджет
            data["budget_rub"] = amount

    # Перелёт: «не больше 5 часов», «до 4 часов в пути».
    if m := re.search(r"(?:не больше|не дольше|до|максимум)\s*(\d+)\s*час", t):
        data["max_flight_minutes"] = int(m.group(1)) * 60
    if re.search(r"без пересад|прям\w* рейс|прям\w* перел", t):
        data["direct_only"] = True
    if re.search(r"без виз", t):
        data["visa_free_only"] = True
    if m := re.search(r"([1-5])\s*(?:\*|зв[её]зд)", t):
        data["min_stars"] = int(m.group(1))

    # Состав: «двое взрослых», «вдвоем», «с женой», «ребенку 5 лет».
    if m := re.search(r"(\d+|[а-я]+)\s+взросл", t):
        if (n := _number(m.group(1))) is not None:
            data["adults"] = n
    elif re.search(r"втроем", t):
        infer("adults", 3)
    elif re.search(r"вдвоем|с женой|с мужем|с девушкой|с парнем|с подругой|с другом|с супруг", t):
        infer("adults", 2)
    ages = [int(a) for a in re.findall(r"\b(\d{1,2})\s*(?:лет|года|год)\b", t) if int(a) <= 17]
    ages += [int(a) for a in re.findall(r"\b(\d{1,2})-?летн", t) if int(a) <= 17]
    if ages:
        data["children_ages"] = sorted(set(ages), key=ages.index)

    needs = detect_needs(text)
    for rule in RULES:
        if rule.need in needs:
            for name, value in rule.sets.items():
                infer(name, value)
    if data.get("children_ages") and "child" not in needs:
        needs.append("child")
    if "child" in needs and "adults" not in data:
        # «С ребёнком» чаще всего значит двое взрослых; допущение видно в брифе и правится одним нажатием.
        infer("adults", 1 if re.search(r"\b(одна|один|сама|сам)\b", t) else 2)

    return BriefPatch(**data, needs_add=needs, inferred=list(dict.fromkeys(inferred)))
