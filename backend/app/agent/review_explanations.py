"""D7 explanations from a selected typed profile; no model calls or state changes."""

import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.agent.session import Session
from app.contracts.hotel_profiles import HotelReviewProfile
from app.contracts.identifiers import canonical_hotel_id

ASPECT_NAMES = {
    "children": "Дети",
    "noise": "Тишина/шум",
    "cleanliness": "Чистота",
    "food": "Питание",
    "staff": "Персонал",
    "location": "Расположение",
    "wifi": "Wi-Fi",
    "pets": "Животные",
}
SEGMENT_NAMES = {"family": "семьи", "couple": "пары", "business": "деловые поездки", "pets": "с животными"}
ASPECT_ORDER = {
    "family": ("children", "noise", "cleanliness", "food", "location", "staff", "wifi", "pets"),
    "couple": ("noise", "cleanliness", "location", "staff", "food", "wifi", "children", "pets"),
    "business": ("wifi", "noise", "location", "staff", "cleanliness", "food", "children", "pets"),
    "pets": ("pets", "noise", "location", "cleanliness", "staff", "food", "wifi", "children"),
}


@dataclass(frozen=True)
class ReviewExplanation:
    text: str
    facts: dict[str, Any]


def _requested(text: str) -> bool:
    return "отзыв" in text or bool(
        re.search(
            r"(?:почему|чем|подходит|подойдет|подойд[её]т|что говорят).{0,90}(?:отел|вариант|семь|пар[аые]|животн)",
            text,
        )
        or re.search(r"(?:этот|этого|этим)\s+отел", text)
    )


def _segment(text: str, session: Session) -> str | None:
    if re.search(r"животн|собак|кошк|питом", text):
        return "pets"
    if re.search(r"семь|семе|ребен|дет[яье]|дети", text):
        return "family"
    if re.search(r"пар[аыуе]|вдвоем|романт|молодожен", text):
        return "couple"
    if re.search(r"командиров|делов|работ|бизнес", text):
        return "business"
    if session.brief.children_ages:
        return "family"
    if session.brief.pets:
        return "pets"
    return None


def _option(text: str, session: Session) -> tuple[int, str]:
    match = re.search(r"(?:вариант(?:а|е)?|отел(?:ь|я|е))\s*(?:номер\s*)?#?\s*(\d+)\b", text)
    if match:
        return int(match[1]), "explicit"
    for number, word in enumerate(("перв", "втор", "трет"), 1):
        if re.search(rf"\b{word}\w*\s+(?:вариант|отел)", text):
            return number, "explicit"
    if session.selected is not None:
        return session.selected, "selected"
    return 1, "default_first"


def explain_hotel_reviews(session: Session, text: str) -> ReviewExplanation | None:
    normalized = text.casefold().replace("ё", "е")
    if not _requested(normalized):
        return None
    option, selection = _option(normalized, session)
    package = session.package(option)
    facts: dict[str, Any] = {
        "status": "unknown",
        "option": option,
        "selection_source": selection,
        "segment": _segment(normalized, session),
        "changes_state": False,
    }
    if package is None:
        return ReviewExplanation("Сначала найдите или выберите вариант: профиля конкретного отеля пока нет.", facts)
    hotel = package.get("hotel", {})
    facts.update(hotel_id=hotel.get("hotel_id"), hotel_name=hotel.get("name"))
    prefix = f"Вариант {option}, отель {hotel.get('name', 'без названия')}. "
    if selection == "default_first":
        prefix += "Вариант не выбран: для объяснения беру первый из показанных. "
    if session.stale:
        facts["reason"] = "stale_options"
        return ReviewExplanation(prefix + "Вариант устарел после изменения условий; сначала повторите поиск.", facts)
    raw_profile = hotel.get("review_profile")
    if raw_profile is None:
        facts["reason"] = "missing_profile"
        return ReviewExplanation(prefix + "Профиль отзывов отсутствует: числа и цитаты неизвестны.", facts)
    try:
        profile = HotelReviewProfile.model_validate(raw_profile)
        if profile.hotel_id != canonical_hotel_id(hotel.get("hotel_id", "")):
            raise ValueError("Profile hotel mismatch")
    except (ValidationError, ValueError, TypeError):
        facts["reason"] = "invalid_or_mismatched_profile"
        return ReviewExplanation(
            prefix + "Профиль не прошёл проверку или относится к другому отелю; его выводы не использую.", facts
        )
    segment = facts["segment"]
    guest = profile.guest_profiles[segment] if segment else None
    rows = guest.aspects if guest else profile.aspects
    count = guest.review_count if guest else profile.review_count
    order = ASPECT_ORDER.get(segment, tuple(ASPECT_NAMES))
    counts = [
        {
            "aspect": aspect,
            "positive_count": rows[aspect].positive_count,
            "negative_count": rows[aspect].negative_count,
            "review_count": rows[aspect].review_count,
            "mention_count": rows[aspect].mention_count,
        }
        for aspect in order
        if aspect in rows
    ]
    evidence = []
    for sentiment in ("positive", "negative"):
        found = next(
            (
                (aspect, item)
                for aspect in order
                if aspect in rows
                for item in rows[aspect].evidence
                if item.sentiment == sentiment
            ),
            None,
        )
        if found is not None:
            aspect, item = found
            evidence.append({"aspect": aspect, **item.model_dump()})
    facts.update(
        status="available",
        is_synthetic=True,
        profile_review_count=profile.review_count,
        segment_review_count=count,
        extraction=profile.extraction.model_dump(),
        counts=counts,
        evidence=evidence,
    )
    label = SEGMENT_NAMES[segment] if segment else "все группы гостей"
    parts = [prefix, f"В профиле {profile.review_count} синтетических отзывов; группа «{label}»: {count}."]
    parts.append("Положительные и отрицательные числа ниже — упоминания, а не оценка отеля или число всех гостей.")
    parts.extend(
        f"{ASPECT_NAMES[row['aspect']]}: положительных {row['positive_count']}, "
        f"отрицательных {row['negative_count']} (отзывов с этим аспектом: {row['review_count']})."
        for row in counts
    )
    if not counts:
        parts.append("В этой группе нет подтверждённых упоминаний; пригодность неизвестна.")
    for item in evidence:
        title = "Плюс" if item["sentiment"] == "positive" else "Риск"
        parts.append(f"{title}: «{item['quote']}» [review_id: {item['review_id']}].")
    if not any(item["sentiment"] == "negative" for item in evidence):
        parts.append("Отрицательных упоминаний в выбранном срезе нет; это не доказывает отсутствие рисков.")
    if segment == "family":
        rule = hotel.get("adults_only")
        parts.append(
            "Правила отеля: "
            + (
                "проживание с детьми запрещено."
                if rule is True
                else "дети допускаются по справочнику условий, но отзывы это правило не подтверждают."
                if rule is False
                else "принимает ли отель детей — неизвестно, требуется уточнение."
            )
        )
    elif segment == "pets":
        rule = hotel.get("pets_allowed")
        parts.append(
            "Правила отеля: "
            + (
                "животные допускаются по справочнику условий, но отзывы это правило не подтверждают."
                if rule is True
                else "животные запрещены."
                if rule is False
                else "проживание с животными неизвестно, требуется уточнение."
            )
        )
    parts.append("Это синтетические тестовые отзывы, не отзывы реальных гостей. " + profile.notice)
    if profile.extraction.method == "lexical-fixture-baseline-v1":
        parts.append("Извлечение выполнено правилами (baseline); настоящий LLM-прогон не выполнялся.")
    else:
        parts.append(
            f"Метод извлечения: {profile.extraction.method}; "
            f"live_model_run={str(profile.extraction.live_model_run).lower()}."
        )
    return ReviewExplanation("\n".join(parts), facts)
