"""Названия городов и стран → IATA-коды, которые понимают инструменты D1.

Модель может написать «Питер», «Шереметьево» или «Мальдивы». Инструментам нужен код,
поэтому всё, что модель предложила как место, проходит через этот справочник.
Неизвестное место не попадает в бриф: лучше переспросить, чем искать не там.
Справочник покрывает демо-каталог D1; новые направления добавляются сюда же (D5).
"""

import re

# Код → человекочитаемое название для ответов пользователю.
PLACE_NAMES: dict[str, str] = {
    "MOW": "Москва",
    "SVO": "Москва (Шереметьево)",
    "LED": "Санкт-Петербург",
    "AYT": "Анталья",
    "DXB": "Дубай",
    "MLE": "Мальдивы",
}

# Регулярные выражения по основам слов: «из Москвы», «в Анталью», «на Мальдивах».
# «мале» требует конца слова, чтобы «маленький» не превращался в Мальдивы.
_ALIASES: tuple[tuple[str, str], ...] = (
    ("шереметьев", "SVO"),
    ("москв", "MOW"),
    ("moscow", "MOW"),
    (r"мск\b", "MOW"),
    ("питер", "LED"),
    ("петербург", "LED"),
    ("спб", "LED"),
    ("пулков", "LED"),
    ("st petersburg", "LED"),
    ("анталь", "AYT"),
    ("анталий", "AYT"),
    ("турци", "AYT"),
    ("antalya", "AYT"),
    ("turkey", "AYT"),
    ("дуба", "DXB"),
    ("оаэ", "DXB"),
    ("эмират", "DXB"),
    ("dubai", "DXB"),
    ("uae", "DXB"),
    ("мальдив", "MLE"),
    (r"мале\b", "MLE"),
    ("maldives", "MLE"),
    (r"male\b", "MLE"),
    ("нячанг", "CXR"),
    ("nha trang", "CXR"),
)

PLACE_NAMES["CXR"] = "Нячанг"

ORIGINS = frozenset({"MOW", "SVO", "LED"})
DESTINATIONS = frozenset({"AYT", "DXB", "MLE", "CXR"})
# Finite demo bundle coverage, not a claim that any particular offer is available.
# Other D5 destinations provide reference information, not hotel/transfer packages.
FULL_TRIP_DESTINATIONS = frozenset({"AYT", "DXB", "MLE"})


def to_iata(value: str | None) -> str | None:
    """«Питер» → LED, «led» → LED, «Марс» → None."""
    if not value:
        return None
    text = value.strip()
    if re.fullmatch(r"[A-Za-z]{3}", text) and text.upper() in PLACE_NAMES:
        return text.upper()
    lowered = text.lower().replace("ё", "е")
    for stem, code in _ALIASES:
        if re.search(rf"(?<![а-яa-z]){stem}", lowered):
            return code
    return None


def place_name(code: str | None) -> str:
    if code is None:
        return "—"
    return PLACE_NAMES.get(code, code)
