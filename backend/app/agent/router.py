"""Роутер: какой шаг делать дальше. Решает код, модель даёт только намерение.

Этапы пути: сбор брифа → направления → сборка поездки → подтверждение → покупка.

Гарантии:
- без города вылета и месяца ничего не ищем, спрашиваем по одному полю;
- нет направления — предлагаем направления, а не спрашиваем «куда»;
- изменились условия — пересобираем то, что устарело, а не начинаем сначала;
- покупка только со стадии ожидания подтверждения, как бы ни ошиблась модель.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.agent.analysis import MessageAnalysis
from app.agent.session import Session, Stage


class Action(StrEnum):
    ASK = "ask"
    SUGGEST = "suggest_destinations"
    SEARCH = "search_trip"
    EXPLAIN = "explain"
    ASK_WHICH = "ask_which_option"
    CONFIRM = "request_confirmation"
    BOOK = "book"
    CANCEL = "cancel_confirmation"
    OFF_TOPIC = "off_topic"


# Без этого нельзя двигаться дальше. Порядок — порядок вопросов.
REQUIRED: tuple[str, ...] = ("month", "origin")


@dataclass
class Decision:
    action: Action
    reason: str
    field: str | None = None
    option: int | None = None


def missing_fields(session: Session) -> list[str]:
    brief = session.brief
    missing = [name for name in REQUIRED if getattr(brief, name) is None]
    if brief.transport == "train":
        if brief.destination is None:
            missing.append("destination")
        if brief.start_date is None:
            missing.append("start_date")
    # Возраст детей меняет цену и правила отеля, а угадать его нельзя.
    if "child" in brief.needs and not brief.children_ages:
        missing.append("children_ages")
    return missing


def decide(session: Session, analysis: MessageAnalysis, *, search_changed: bool) -> Decision:
    intent = analysis.intent
    stage = session.stage

    if stage is Stage.AWAITING_CONFIRMATION:
        if intent == "confirm":
            return Decision(Action.BOOK, "пользователь подтвердил", option=session.selected)
        if intent == "decline":
            return Decision(Action.CANCEL, "пользователь отказался от оформления")
        if intent == "select" and analysis.option and analysis.option != session.selected:
            return Decision(Action.CONFIRM, "выбран другой вариант", option=analysis.option)

    if intent == "other" and not search_changed:
        return Decision(Action.OFF_TOPIC, "сообщение не о поездке")

    if intent in ("select", "confirm") and stage is Stage.PACKAGES_SHOWN and not session.stale:
        option = analysis.option or (1 if len(session.packages) == 1 else None)
        if option is None or option > len(session.packages):
            return Decision(Action.ASK_WHICH, "непонятно, какой вариант выбран")
        return Decision(Action.CONFIRM, "выбран вариант, нужно подтверждение", option=option)

    if intent == "question" and not search_changed and (session.packages or session.destinations or session.trains):
        return Decision(Action.EXPLAIN, "вопрос по найденным вариантам")

    missing = missing_fields(session)
    if missing:
        return Decision(Action.ASK, f"не хватает: {', '.join(missing)}", field=missing[0])

    if session.brief.destination is None:
        if session.destinations and not session.stale:
            return Decision(Action.EXPLAIN, "направления уже показаны, условия не менялись")
        return Decision(Action.SUGGEST, "направление не выбрано")

    if (session.packages or session.trains) and not session.stale:
        return Decision(Action.EXPLAIN, "условия не менялись, комментирую текущие варианты")
    return Decision(Action.SEARCH, "условия изменились, пересобираю" if session.packages else "данных достаточно")
