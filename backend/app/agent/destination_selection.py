"""Conservative selection of finite D5 labels, independent of model wording.

An alias identifies the catalogue's anchor airport, not flight/hotel coverage.
Ordinary questions and mentions of places never become a destination patch.
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

from app.tools.destinations import Catalogue, load_catalogue

_DASHES = str.maketrans(
    {chr(code): "-" for code in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE58, 0xFE63, 0xFF0D)}
)
_QUESTIONS = re.compile(
    r"\b(?:почему|зачем|если|как|что|сколько|какой|какая|какие|где|расскажи|можно\s+ли|стоит\s+ли)\b"
)
_NEGATION = re.compile(r"\bне\s+(?:хочу|хотим|еду|едем|поеду|поедем|летим|лечу|полетим|полечу|ехать|лететь)\b")
_ACTION = (
    r"(?:(?:а|теперь)\s+)*(?:(?:я|мы)\s+)?(?:хочу|хотим|давай|давайте|еду|едем|поеду|поедем|лечу|летим|полечу|полетим)"
)
_ROUTE = re.compile(r"^(?:" + _ACTION + r"\s+)?(?:поездку\s+)?из\s+[^,;.!?]+?\s+(?:в|во|на)\s+(.+)$")
_DESTINATION = re.compile(r"^" + _ACTION + r"\s+(?:(?:поехать|полететь|отправиться|поездку)\s+)?(?:в|во|на)\s+(.+)$")
_CHANGE = re.compile(r"^(?:смени|измени|поменяй|замени)\s+(?:направление|город|курорт)\s+(?:в|во|на)\s+(.+)$")
_CORRECTION = re.compile(r"(?:^|[\s,;])(?:точнее|вернее|но\s+лучше)\s+(?:в|во|на)\s+")
_UNCLEAR_CORRECTION = re.compile(r"\b(?:точнее|вернее|вместо|лучше|передумал|передумала|передумали|но)\b")


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е").translate(_DASHES)
    value = re.sub(r"(?<=[^\W\d_])\s*-\s*(?=[^\W\d_])", "-", value)
    return " ".join(value.split())


def _word_character(value: str) -> bool:
    return value in "_-" or unicodedata.category(value)[0] in "LMN"


def _whole_label_present(text: str, label: str) -> bool:
    start = 0
    while (start := text.find(label, start)) != -1:
        end = start + len(label)
        if (start == 0 or not _word_character(text[start - 1])) and (
            end == len(text) or not _word_character(text[end])
        ):
            return True
        start += len(label)
    return False


def resolve_catalogue_destination(
    text: str, *, catalogue: Catalogue | Mapping | None = None, data_path: str | Path | None = None
) -> str | None:
    """Resolve an explicit choice or a bare exact D5 name/alias/IATA.

    Longest whole labels win; equal normalized labels with different anchors are
    ambiguous. Countries are not added as aliases. Source/configuration errors
    remain errors, as with D5's public resolver, rather than inventing a place.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    if catalogue is not None and data_path is not None:
        raise ValueError("Pass either catalogue or data_path, not both")
    records = (
        load_catalogue(data_path)
        if catalogue is None
        else (catalogue if isinstance(catalogue, Catalogue) else Catalogue.model_validate(catalogue))
    )
    normalized = _normalize(text).strip(" .!,;:\"'«»")
    info = re.fullmatch(r"расскажи\s+о\s+направлении\s+([a-z]{3})\??", normalized)
    if info:
        code = info[1].upper()
        return code if any(row.iata == code for row in records.destinations) else None
    if "?" in normalized or _QUESTIONS.search(normalized) or _NEGATION.search(normalized):
        return None
    if re.search(r"\b(?:или|либо)\b", normalized):
        return None

    labels: dict[str, set[str]] = defaultdict(set)
    for row in records.destinations:
        for label in (row.name, row.iata, *row.aliases):
            labels[_normalize(label)].add(row.iata)
        # The only inferred grammatical forms are the ordinary accusative of
        # canonical Russian names: «Шри-Ланка» -> «в Шри-Ланку». No stemming or
        # substring matching is used for aliases or arbitrary user words.
        if re.fullmatch(r"[а-яё -]+", row.name.casefold()) and row.name[-1:].casefold() in {"а", "я"}:
            ending = "у" if row.name[-1:].casefold() == "а" else "ю"
            labels[_normalize(row.name[:-1] + ending)].add(row.iata)
        if row.iata == "CMB" and _normalize(row.name) == "шри-ланка":
            labels["шри-ланке"].add(row.iata)

    match = _ROUTE.fullmatch(normalized) or _DESTINATION.fullmatch(normalized) or _CHANGE.fullmatch(normalized)
    if match:
        target, allow_details = match[1], True
    else:
        preposition = re.fullmatch(r"(?:в|во|на)\s+(.+)", normalized)
        target, allow_details = (preposition[1], False) if preposition else (normalized, False)
    # Only an explicit, unnegated correction selects its last target. A generic
    # mention of several cities is left to clarification instead of taking the
    # first city and overwriting the model's later interpretation.
    corrections = list(_CORRECTION.finditer(target)) if match or preposition else []
    if corrections:
        target = target[corrections[-1].end() :]
    target = target.strip(" .!,;:\"'«»")
    if _UNCLEAR_CORRECTION.search(target):
        return None
    for label in sorted(labels, key=len, reverse=True):
        if not target.startswith(label):
            continue
        remainder = target[len(label) :]
        if remainder and (_word_character(remainder[0]) or not allow_details):
            continue
        # A hotel/brand title is not an exact city label. Additional unpunctuated
        # text is accepted only when it starts an ordinary trip criterion.
        if (
            remainder
            and remainder[0].isspace()
            and not re.match(
                r"\s+(?:\d|в\b|во\b|на\b|с\b|со\b|без\b|из\b|только\b|дата\b|вылет\b|бюджет\b|пожалуйста\b)", remainder
            )
        ):
            continue
        codes = labels[label]
        if len(codes) != 1:
            return None
        code = next(iter(codes))
        if any(codes != {code} and _whole_label_present(remainder, other) for other, codes in labels.items()):
            return None
        return code
    return None
