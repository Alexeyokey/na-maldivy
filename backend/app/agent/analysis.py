"""Разбор сообщения: модель в JSON-режиме плюс правила из hidden.py.

Модель понимает язык, правила гарантируют, что скрытые требования из словаря не потеряются:
к ответу модели добавляются только они и выведенные из них поля (``hidden_only``).
Если модель не ответила или вернула негодный JSON дважды, работают одни правила: агент
теряет в качестве разбора, но не падает и не портит бриф.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.agent.brief import FIELD_LABELS, BriefPatch, TripBrief, merge_patches
from app.agent.hidden import NEEDS, extract, hidden_only
from app.agent.prompts import ANALYZE_PROMPT, context_message
from app.llm import LLMClient, LLMError, Message

log = logging.getLogger(__name__)

Intent = Literal["plan", "inspiration", "modify", "question", "select", "confirm", "decline", "other"]


class MessageAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Intent = "plan"
    patch: BriefPatch = Field(default_factory=BriefPatch)
    option: int | None = Field(default=None, ge=1, le=10)
    contradictions: list[str] = Field(default_factory=list)

    @field_validator("patch")
    @classmethod
    def _known_codes(cls, patch: BriefPatch) -> BriefPatch:
        # Модель не может придумать новое требование или поле: неизвестные коды отбрасываем.
        patch.needs_add = [n for n in patch.needs_add if n in NEEDS]
        patch.needs_remove = [n for n in patch.needs_remove if n in NEEDS]
        patch.cleared = [n for n in patch.cleared if n in FIELD_LABELS]
        patch.inferred = [n for n in patch.inferred if n in FIELD_LABELS]
        return patch


@dataclass
class AnalysisOutcome:
    analysis: MessageAnalysis
    source: Literal["llm", "rules"]
    warnings: list[str] = field(default_factory=list)


_CONFIRM = r"^\s*(да|ага|угу|ок|окей|давай|подтверждаю|оформля\w*|бронир\w*|оплач\w*)\b"
_DECLINE = r"^\s*(нет|не надо|отмен\w*|стоп|передумал\w*)\b"
_ORDINALS = {"перв": 1, "втор": 2, "трет": 3, "1": 1, "2": 2, "3": 3}
_TRAVEL_WORDS = r"поезд|поездк|отдых|отпуск|лет\w*|отел|мор|билет|куда|тур|виз|рейс|перел|трансфер|ночей|недел"


def requests_fabricated_offers(text: str) -> bool:
    normalized = text.casefold().replace("ё", "е")
    return bool(
        re.search(r"(?:забей|игнорируй|не учитывай).{0,30}(?:реальн|данн|налич)", normalized)
        or re.search(r"(?:выдумай|придумай).{0,35}(?:налич|цен|билет|предложен)", normalized)
        or re.search(r"(?:напиши|скажи).{0,15}что.{0,80}(?:за 1 руб|за 1 р\b|за 1 ₽|люкс в любом)", normalized)
    )


def rules_intent(text: str, patch: BriefPatch, has_options: bool, awaiting_confirmation: bool) -> MessageAnalysis:
    """Намерение без модели: только явные сигналы, иначе plan."""
    t = text.lower().replace("ё", "е")
    option = None
    if m := re.search(r"\b(перв|втор|трет|[123])\w*\s*(вариант)?", t):
        if has_options and (m.group(2) or re.search(r"бер[уе]|выбира|давай|хочу", t)):
            option = _ORDINALS[m.group(1)]
    if awaiting_confirmation and re.search(_CONFIRM, t):
        return MessageAnalysis(intent="confirm", patch=patch)
    if awaiting_confirmation and re.search(_DECLINE, t):
        return MessageAnalysis(intent="decline", patch=patch)
    if option is not None:
        return MessageAnalysis(intent="select", patch=patch, option=option)
    if not patch.is_empty():
        return MessageAnalysis(intent="modify" if has_options else "plan", patch=patch)
    if re.search(r"куда (бы )?(поехать|съездить|полететь)|не знаю куда|идеи|посоветуй", t):
        return MessageAnalysis(intent="inspiration", patch=patch)
    if "?" in t or re.search(r"^(почему|а почему|зачем|сколько|что|как)\b", t):
        return MessageAnalysis(intent="question", patch=patch)
    if not re.search(_TRAVEL_WORDS, t):
        return MessageAnalysis(intent="other", patch=patch)
    return MessageAnalysis(intent="plan", patch=patch)


async def analyze_message(
    llm: LLMClient | None,
    text: str,
    *,
    brief: TripBrief,
    history: list[Message],
    today: date,
    has_options: bool,
    awaiting_confirmation: bool,
) -> AnalysisOutcome:
    rules_patch = extract(text, today, brief)
    if llm is None:
        return AnalysisOutcome(rules_intent(text, rules_patch, has_options, awaiting_confirmation), "rules")

    messages = [
        Message(role="system", content=ANALYZE_PROMPT),
        context_message(
            "analyze",
            today,
            brief=brief.model_dump(mode="json"),
            has_options=has_options,
            awaiting_confirmation=awaiting_confirmation,
        ),
        *history,
        Message(role="user", content=text),
    ]
    warnings: list[str] = []
    for attempt in range(2):
        try:
            response = await llm.complete(messages, tier="fast", json_mode=True, temperature=0)
            analysis = MessageAnalysis.model_validate(json.loads(response.text or ""))
        except LLMError as exc:
            log.warning("Разбор моделью не удался: %s", exc)
            warnings.append("Модель не ответила, разбираю сообщение правилами")
            break
        except (json.JSONDecodeError, ValidationError) as exc:
            log.warning("Модель вернула негодный разбор (попытка %d): %s", attempt + 1, exc)
            if attempt == 0:
                # Один повтор: показываем модели её ответ и ошибку.
                messages += [
                    Message(role="assistant", content=response.text),
                    Message(role="user", content=f"Ответ не прошёл проверку: {exc}. Верни исправленный JSON."),
                ]
                continue
            warnings.append("Модель дважды вернула негодный разбор, разбираю сообщение правилами")
            break
        if analysis.intent not in ("question", "other"):
            analysis.patch = merge_patches(analysis.patch, hidden_only(rules_patch))
        return AnalysisOutcome(analysis, "llm", warnings)

    return AnalysisOutcome(rules_intent(text, rules_patch, has_options, awaiting_confirmation), "rules", warnings)
