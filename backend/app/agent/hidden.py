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
from calendar import monthrange
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
    "flexible": Need("планы могут поменяться", "возвратные тарифы; сроки и удержания нужно уточнить"),
    "surfing": Need("сёрфинг", "учитываем пожелание; наличие спотов, школ и условия занятий не подтверждены"),
    "diving": Need("дайвинг", "учитываем пожелание; доступность погружений и снаряжения нужно уточнить"),
    "active": Need("активный отдых", "сохраняем при подборе; конкретные активности и их стоимость не подтверждены"),
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
    Rule("surfing", r"\bс[её]рф|\bsurf"),
    Rule("diving", r"\bдайв|\bdiving"),
    Rule("active", r"активн\w*\s+отдых|отдых\w*\s+активн"),
    Rule(
        "flexible",
        r"бесплатн\w* отмен|с возвратом|\bвозвратн|деньги.{0,30}верн\w*.{0,20}отмен|"
        r"планы могут|могут поменяться|не уверен\w* в дат",
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


def _add_months(day: date, months: int) -> date:
    offset = day.year * 12 + day.month - 1 + months
    year, month = divmod(offset, 12)
    month += 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def _flexible_dates(text: str, today: date) -> tuple[dict[str, Any], list[str]]:
    """Keep every stated month and bound an explicitly open-ended date choice.

    A month without a year means its next future occurrence, not the first
    month in the dictionary. A generic «любой месяц» uses a disclosed six-month
    assumption; an explicit half-year is exactly six calendar months.
    """
    months: list[str] = []
    for stem, month in _MONTHS:
        for match in re.finditer(rf"\b{stem}[а-я]*(?:\s+(\d{{4}})\b)?", text):
            year = int(match.group(1)) if match.group(1) else today.year + (month < today.month)
            months.append(f"{year:04d}-{month:02d}")
    values: dict[str, Any] = {}
    if len(set(months)) > 1:
        values["departure_months"] = sorted(set(months))

    explicit_half_year = bool(
        re.search(
            r"(?:ближайш\w*|следующ\w*|предстоящ\w*|течение)\s+(?:полгода|полугод\w*|6\s+месяц\w*|шест\w*\s+месяц\w*)",
            text,
        )
    )
    any_dates = bool(re.search(r"\bлюб\w*\s+(?:дат\w*|месяц\w*|время)|\bдат\w*\s+не\s+важн", text))
    if explicit_half_year or any_dates and not months:
        values.update(
            departure_window_start=(today + timedelta(days=1)).isoformat(),
            departure_window_end=_add_months(today, 6).isoformat(),
        )
    inferred = (
        ["departure_window_start", "departure_window_end"]
        if any_dates and not explicit_half_year and not months
        else []
    )
    return values, inferred


def _money(amount: str, unit: str | None) -> int:
    value = float(amount.replace(" ", "").replace(",", "."))
    if unit and unit.startswith(("тыс", "т", "k", "к")):
        value *= 1_000
    elif unit and unit.startswith(("млн", "миллион")):
        value *= 1_000_000
    return int(value)


def _budget(text: str) -> int | None:
    """Small amounts require explicit money context; time/year numbers are not money."""
    amount = r"(-?\d[\d\s]*[.,]?\d*)"
    scale = r"\s*(тыс\w*|т\.?р\b|k\b|к\b|млн\b|миллион\w*)?"
    explicit = re.search(
        r"\b(?:бюджет\w*|потрачу|потратим|потратить)\s*"
        r"(?:(?:теперь|сейчас|максимум)\s*)?[:=]?\s*(?:на\s*)?" + amount + scale,
        text,
    )
    general = re.search(r"(?:\bдо|\bза|не дороже|в пределах)\s*" + amount + scale, text)
    for match, money_context in ((explicit, True), (general, False)):
        if match is None:
            continue
        tail = text[match.end() :]
        if re.match(r"\s*(?:час|минут|дн\w*|день|дня|сут|ноч|лет\b|год|г\b|рейс|мест)", tail):
            continue
        rubles = bool(re.match(r"\s*(?:руб\w*|р\.?(?=\s|$)|₽)", tail))
        value = _money(match.group(1).strip(), match.group(2))
        if money_context or rubles or match.group(2) or value >= 5_000:
            return value
    return None


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


def requests_checkout_cancellation(text: str) -> bool:
    """Close only an explicitly stopped whole checkout, never a question or extra."""
    t = text.casefold().replace("ё", "е").strip()
    if "?" in t or re.search(r"\b(?:если|можно ли|что будет|как отменить)\b", t):
        return False
    if re.search(
        r"\bне\s+(?:отменя\w*|отменить|останавлива\w*)|"
        r"\b(?:отменить|отменя\w*|останавливать)\s+не\s+(?:надо|нужно|хочу)|"
        r"\b(?:платить|оплат\w*)\s+за\b",
        t,
    ):
        return False
    t = re.sub(r"^пожалуйста[,\s]+", "", t)
    command = re.split(r"[,.!;]", t, maxsplit=1)[0].strip()
    if re.fullmatch(r"(?:я\s+)?не буду платить|стоп|не (?:оформляй|бронируй)", command):
        return True
    target = r"(?:оформлен\w*|корзин\w*|оплат\w*|брон\w*|заказ\w*|поездк\w*)"
    return bool(
        re.fullmatch(r"(?:отмени|отменить|отменяй|отмените)(?:\s+пожалуйста)?", command)
        or re.fullmatch(
            r"(?:(?:хочу|прошу)\s+)?(?:отмени\w*|отменить|прекрати\w*|останови\w*)\s+"
            r"(?:(?:это|этот|эту|текущее|текущую|незавершенное|мое|мою)\s+)?"
            + target
            + r"(?:\s+(?:пожалуйста|сейчас))?",
            command,
        )
    )


def quick_refinement_requested(text: str) -> bool:
    normalized = " ".join(text.casefold().replace("ё", "е").strip(" .!\n\t").split())
    return normalized in {
        "подбери более дешевые варианты на тех же условиях, без оформления",
        "только прямые рейсы, остальные условия сохраняем, без оформления",
        "только возвратный отель, остальные условия сохраняем, без оформления",
        "можно с пересадками, остальные условия сохраняем, без оформления",
        "можно и невозвратные отели, остальные условия сохраняем, без оформления",
        "сними приоритет низкой цены, остальные условия сохраняем, без оформления",
    }


def unrecognized_destination(text: str, resolved: str | None = None, *, catalogue=None, data_path=None) -> str | None:
    """Retire an old trip only for an unambiguous geographic destination.

    The existing finite D5 catalogue identifies actual city aliases. Other words
    after «в/на» can describe a date, budget, room or meal and must not erase the
    route. A city/country label alone is not evidence of a named destination.
    """
    t = text.casefold().replace("ё", "е")
    if "?" in t or re.match(r"^\s*(?:почему|зачем|расскажи|объясни|хочу узнать)\b", t):
        return None
    origin_given = any(to_iata(match.group(1)) in ORIGINS for match in re.finditer(r"\bиз\s+([а-яa-z-]+)", t))
    if not origin_given and not re.search(
        r"\b(?:хочу|хотим|давай\w*|поед\w*|едем|еду|полет\w*|полечу|летим|лечу|вместо|поменяй|смени)\b", t
    ):
        return None
    from app.tools.destinations import resolve_iata

    for match in re.finditer(r"\b(?:в|во|на)\s+([а-яa-z-]+)", t):
        target = match.group(1)
        start, end = match.span(1)
        if target in {"город", "страну", "курорт"}:
            named = re.match(r"\s+([а-яa-z-]+)", t[match.end() :])
            if not named:
                continue
            target = named.group(1)
            start, end = match.end() + named.start(1), match.end() + named.end(1)
        if to_iata(target) or re.fullmatch(r"[a-z]{3}", target):
            continue
        try:
            matches = resolve_iata(target, catalogue=catalogue, data_path=data_path)
        except (OSError, ValueError):
            continue  # An ambiguous/ordinary word cannot invalidate the old trip.
        if resolved and resolved.upper() in matches:
            continue
        return text[start:end][:64]
    return None


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
    delta_direction = re.search(r"уменьш\w*|сократ\w*|сниз\w*|увелич\w*|повыс\w*", t)
    if (
        money
        and brief.budget_rub
        and (money.group(1) or re.search(r"дешевле|дороже", money.group(0)) or delta_direction)
    ):
        amount = _money(money.group(2).strip(), money.group(3))
        less = (
            money.group(1) == "меньше"
            or "дешевле" in money.group(0)
            or bool(delta_direction and re.match(r"уменьш|сократ|сниз", delta_direction.group()))
        )
        data["budget_rub"] = max(1, brief.budget_rub + (-amount if less else amount))
    return data


def _child_ages(text: str, brief: TripBrief | None) -> list[int]:
    """Read each child's age once, preserving siblings with the same age.

    A shared unit in ``5 и 8 лет`` belongs to both numbers.  Longer matches
    reserve their span so the final ``8 лет`` cannot become a third child.
    Ages are meaningful in a family request or its age follow-up; years and
    durations in an unrelated message must not add a child to the trip.
    """
    family = "child" in detect_needs(text) or bool(brief and "child" in brief.needs)
    if not family:
        return []
    matches: list[tuple[int, int, list[int]]] = []

    def add(match: re.Match[str], ages: list[int]) -> None:
        if any(match.start() < end and start < match.end() for start, end, _ in matches):
            return
        # ``через 5 лет`` and ``на 5 лет`` describe time, not an exact age.
        prefix = text[max(0, match.start() - 24) : match.start()]
        if re.search(
            r"\b(?:через|спустя|на|до|от|старше|младше|мне|нам|я|взрослому|взрослым|взрослых|взрослые|"
            r"мужу|жене|маме|папе|родителям)\s*[,:(]?\s*$",
            prefix,
        ):
            # Reserve an adult/duration list too, so its final age cannot be
            # re-read as an isolated child by the next, shorter pattern.
            matches.append((match.start(), match.end(), []))
            return
        # Keep invalid exact ages together: TripBrief rejects the whole field
        # rather than silently pricing only the valid siblings in a list.
        if ages:
            matches.append((match.start(), match.end(), ages))

    # The count is explicit; do not invent a second age from a lone age.
    counted = r"\b(\d{1,2}|[а-я]+)\s+(?:дет(?:ей|и|ьми)|ребен(?:ок|ка|ку|ком))\s+по\s+(\d{1,2})\s*(?:лет|года|год)\b"
    for match in re.finditer(counted, text):
        count = _number(match.group(1))
        if count and count > 0:
            # Nine entries are enough for TripBrief's max-eight validation.
            add(match, [int(match.group(2))] * min(count, 9))
    listed = r"(?<!\d)\b(\d{1,2}(?:\s*(?:,|\bи\b)\s*\d{1,2})+)\s*(?:лет|года|год)\b"
    for match in re.finditer(listed, text):
        add(match, [int(age) for age in re.findall(r"\d{1,2}", match.group(1))])
    for pattern in (r"(?<!\d)\b(\d{1,2})\s*(?:лет|года|год)\b", r"(?<!\d)\b(\d{1,2})-?летн"):
        for match in re.finditer(pattern, text):
            add(match, [int(match.group(1))])
    return [age for _, _, ages in sorted(matches) for age in ages]


def extract(text: str, today: date, brief: TripBrief | None = None) -> BriefPatch:
    """Разбирает типовые фразы. Не угадывает: чего нет в тексте, того нет и в патче.

    С брифом понимает и относительные изменения («на два дня позже»).
    """
    t = text.lower().replace("ё", "е").replace("\u00a0", " ")
    data: dict[str, Any] = relative(text, brief) if brief else {}
    inferred: list[str] = []
    cleared: list[str] = []
    filter_question = "?" in t or bool(re.match(r"^\s*(?:а\s+)?(?:почему|зачем|нужн\w*\s+ли|можно\s+ли)\b", t))

    def infer(name: str, value: Any) -> None:
        if name not in data:
            data[name] = value
            inferred.append(name)

    # A train query must reach D6 rather than the flight/hotel planner.
    classes = {"купе": "coupe", "плацкарт": "platzkart", "св": "sv", "сидяч": "seated"}
    for word, code in classes.items():
        suffix = "" if word == "св" else r"\w*"
        if re.search(rf"\b{word}{suffix}\b", t):
            data.update(transport="train", seat_class=code)
            break
    if re.search(r"\bпоезд(?:ом|а|е|ы)?\b|железнодорож|\bжд\b", t):
        data["transport"] = "train"
    elif re.search(r"самолет|\bавиа|\bполетим|\bлетим", t):
        data["transport"] = "flight"
    rail = data.get("transport", brief.transport if brief else "flight") == "train"

    # Откуда и куда: «из Москвы», «в Анталью», «на Мальдивы».
    # A bare city answers the outstanding origin question.  Only exact names
    # qualify: explicit ``в Москву`` / ``хочу в Питер`` still change destination.
    if brief and brief.origin is None and (brief.transport == "train" or brief.month is not None):
        bare_origins = {
            "москва": "MOW",
            "moscow": "MOW",
            "mow": "MOW",
            "санкт-петербург": "LED",
            "санкт петербург": "LED",
            "петербург": "LED",
            "питер": "LED",
            "спб": "LED",
            "st petersburg": "LED",
            "led": "LED",
            "шереметьево": "SVO",
            "аэропорт шереметьево": "SVO",
            "svo": "SVO",
        }
        if origin := bare_origins.get(t.strip(" \t\r\n.!?")):
            data["origin"] = origin
    for match in re.finditer(r"\b(из|с|от)\s+([а-яa-z-]+)", t):
        code = to_iata(match.group(2))
        if code in ORIGINS:
            data["origin"] = code
            break
    for match in re.finditer(r"\b(в|во|на)\s+([а-яa-z-]+)", t):
        explicit_iata = bool(re.fullmatch(r"[a-z]{3}", match.group(2)))
        code = to_iata(match.group(2)) or (match.group(2).upper() if explicit_iata else None)
        if code in DESTINATIONS or code in {"MOW", "LED"} or explicit_iata:
            data["destination"] = code
            break
    if "destination" not in data and "origin" not in data and re.fullmatch(r"[A-Z]{3}", text.strip()):
        data["destination"] = text.strip()
    mentioned_cities = list(
        dict.fromkeys(code for word in re.findall(r"[а-яa-z-]+", t) if (code := to_iata(word)) in {"MOW", "LED"})
    )
    if len(mentioned_cities) == 2 and "origin" not in data and "destination" not in data:
        data.update(origin=mentioned_cities[0], destination=mentioned_cities[1])
    if "destination" not in data:
        mentioned = {
            code
            for word in re.findall(r"[а-яa-z-]+", t)
            if (code := to_iata(word)) in DESTINATIONS | {"MOW", "LED"} and code != data.get("origin")
        }
        if len(mentioned) == 1:
            data["destination"] = mentioned.pop()
    if data.get("destination") in {"MOW", "LED"} and "transport" not in data:
        infer("transport", "train")
    elif data.get("destination") in DESTINATIONS and "transport" not in data and rail:
        infer("transport", "flight")
    rail = data.get("transport", brief.transport if brief else "flight") == "train"
    if rail and (data.get("origin") == "SVO" or brief and brief.origin == "SVO" and "origin" not in data):
        data["origin"] = "MOW"
        inferred.append("origin")

    # Когда: «18 ноября», «в ноябре».
    for stem, month in _MONTHS:
        day_match = re.search(rf"\b(\d{{1,2}})\s+{stem}[а-я]*\s*(\d{{4}})?", t)
        if day_match:
            if day_match.group(2):
                # Do not silently move a year explicitly given by the user.
                data["start_date"] = f"{day_match.group(2)}-{month:02d}-{int(day_match.group(1)):02d}"
                break
            if start := _future(month, int(day_match.group(1)), today):
                data["start_date"] = start.isoformat()
                break
        if re.search(rf"\b{stem}", t):
            data["month"] = month
            if year_match := re.search(rf"\b{stem}[а-я]*\s+(\d{{4}})\b", t):
                data["year"] = int(year_match.group(1))
            break
    if iso := re.search(r"\b\d{4}-\d{2}-\d{2}\b", t):
        data["start_date"] = iso.group()
    elif dotted := re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", t):
        data["start_date"] = f"{dotted.group(3)}-{int(dotted.group(2)):02d}-{int(dotted.group(1)):02d}"
    elif short_date := re.search(r"(?<![\w.])(\d{1,2})\.(\d{1,2})(?![\d.])", t):
        prefix, suffix = t[: short_date.start()], t[short_date.end() :]
        money_or_age = re.match(r"\s*(?:руб\w*|₽|тыс\w*|[кk]\b|лет\b|год\w*|градус\w*)", suffix) or re.search(
            r"\b(?:бюджет\w*|цена|до|за)\s*$", prefix
        )
        if not money_or_age and (start := _future(int(short_date.group(2)), int(short_date.group(1)), today)):
            data["start_date"] = start.isoformat()
    # Explicit same-month ranges, including Unicode dash, retain both dates.
    for stem, month in _MONTHS:
        span = re.search(rf"\b(?:с\s+)?(\d{{1,2}})\s*(?:[–—-]|по)\s*(\d{{1,2}})\s+{stem}[а-я]*(?:\s+(\d{{4}}))?", t)
        if span:
            try:
                first = (
                    date(int(span.group(3)), month, int(span.group(1)))
                    if span.group(3)
                    else _future(month, int(span.group(1)), today)
                )
                last = date(first.year, month, int(span.group(2))) if first else None
                if first and last and last > first:
                    data.update(start_date=first.isoformat(), nights=(last - first).days)
            except ValueError:
                pass  # malformed dates are handled by normal field validation
            break

    if "start_date" not in data:
        flexible, flexible_inferred = _flexible_dates(t, today)
        if flexible:
            data.pop("month", None)
            data.pop("year", None)
            data.update(flexible)
            inferred += flexible_inferred

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

    # Relative changes retain precedence; an explicit budget can be below 5000 ₽.
    if "budget_rub" not in data and (budget := _budget(t)) is not None:
        data["budget_rub"] = budget

    # Перелёт: «не больше 5 часов», «до 4 часов в пути».
    if m := re.search(r"(?:не больше|не дольше|до|максимум)\s*(\d+)\s*час", t):
        data["max_flight_minutes"] = int(m.group(1)) * 60
    if not filter_question:
        if duration := re.search(r"\bмаксимальная длительность перелета\s+(\d+)\s+минут\b", t):
            data["max_flight_minutes"] = int(duration.group(1))
        if water := re.search(r"\bтемпература воды не ниже\s+(\d+(?:[.,]\d+)?)\s+градус\w*", t):
            data["min_water_temperature_c"] = float(water.group(1).replace(",", "."))
    if not filter_question and re.search(r"без пересад|прям\w* рейс|прям\w* перел", t):
        data["direct_only"] = True
    if re.search(r"поздно вечером|ночн\w* вылет|вылет.{0,25}вечер|вечерн\w* вылет", t):
        data["late_departure"] = True
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
    elif re.search(
        r"вдвоем|\bдвое\b(?!\s+(?:дет|ребен))|на двоих(?!\s+(?:дет|ребен))|"
        r"с женой|с мужем|с девушкой|с парнем|с подругой|с другом|с супруг",
        t,
    ):
        infer("adults", 2)
    if re.search(r"\b(?:один|одна|сам|сама)\b", t) and re.search(r"еду|поеду|полечу|лечу|теперь|только|хочу", t):
        data["adults"] = 1
    ages = _child_ages(t, brief)
    if ages:
        data["children_ages"] = ages

    needs = detect_needs(text)
    if not filter_question:
        for phrase, need in (
            ("предпочитаю короткий перелет", "short_flight"),
            ("нужна круглосуточная стойка регистрации", "late_arrival"),
            ("предпочитаю короткий трансфер", "short_transfer"),
        ):
            if re.search(r"(?:^|[.!]\s*)" + phrase + r"(?:[.!]|$)", t):
                needs.append(need)
    if not filter_question and re.search(r"\bподбери\s+более\s+дешев\w*\s+вариант\w*", t):
        needs.append("budget_low")
    if filter_question:
        needs = [need for need in needs if need != "flexible"]
    removed: list[str] = []
    if not filter_question:
        if re.search(r"\bсними\s+приоритет\s+низкой\s+цены\b", t):
            removed.append("budget_low")
        if re.search(
            r"\b(?:сними|убери|отмени)\s+(?:ограничен\w*\s+(?:по\s+)?)?бюджет\w*|"
            r"\bбюджет\s+(?:не\s*важен|без\s+ограничен\w*)",
            t,
        ):
            data.pop("budget_rub", None)
            cleared.append("budget_rub")
        if re.search(r"\bможно\s+(?:и\s+)?с\s+пересад\w*", t):
            data["direct_only"] = False
        if re.search(r"\bможно\s+(?:и\s+)?невозвратн\w*", t):
            data["refundable_only"] = False
            removed.append("flexible")
    if re.search(r"без\s+дет(?:ей|ьми)|реб[её]нок\s+не\s+едет|дети\s+не\s+едут", t):
        data["children_ages"] = []
        removed.append("child")
    if re.search(r"без\s+(?:собак\w*|кошк\w*|питомц\w*|животн\w*)|(?:собак\w*|кошк\w*|питомц\w*)\s+не\s+едет", t):
        data["pets"] = 0
        removed.append("pets")
    elif not filter_question and (pets_count := re.search(r"\bпитомцев\s*:\s*(\d+)\b", t)):
        data["pets"] = int(pets_count.group(1))
        if data["pets"] == 0:
            removed.append("pets")
        elif "pets" not in needs:
            needs.append("pets")
    needs = [need for need in needs if need not in removed]
    for rule in RULES:
        if rule.need in needs:
            for name, value in rule.sets.items():
                infer(name, value)
    if data.get("children_ages") and "child" not in needs:
        needs.append("child")
    if "child" in needs and "adults" not in data and (brief is None or brief == TripBrief()):
        # «С ребёнком» чаще всего значит двое взрослых; допущение видно в брифе и правится одним нажатием.
        infer("adults", 1 if re.search(r"\b(одна|один|сама|сам)\b", t) else 2)

    return BriefPatch(
        **data, needs_add=needs, needs_remove=removed, cleared=cleared, inferred=list(dict.fromkeys(inferred))
    )
