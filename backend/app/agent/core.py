"""Агент: один ход диалога от сообщения до ответа (A2).

    analyze (модель + правила) → apply (код) → decide (код) → execute (инструменты, тексты)

Ход работает над копией сессии и сохраняет её только в конце: если что-то упало посередине,
бриф и показанные варианты остаются прежними.

События для интерфейса (формат до O1, поле ``type`` — имя SSE-события):

- ``status``      {stage, text} — что агент делает сейчас («Ищу отели»);
- ``brief``       {brief, sources, changes, rejected, needs} — бриф после разбора;
- ``question``    {field, text, quick_replies} — уточняющий вопрос и кнопки;
- ``tool_call``   {name, arguments} и ``tool_result`` {name, ok, count | error};
- ``options``     {kind: destinations | packages | trains, items, assumptions, notes} — карточки вариантов;
- ``diff``        {before, after} — что поменялось после пересборки;
- ``booking``     {id, ...} — имитация оформления;
- ``warning``     {message} — агент обошёл проблему (модель не ответила и т.п.);
- ``delta``       {text} — кусок текста ответа;
- ``done``        {session_id, stage, version} или ``error`` {message} — конец хода.
"""

import asyncio
import copy
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.agent import formatting as fmt
from app.agent.analysis import MessageAnalysis, analyze_message
from app.agent.brief import FIELD_LABELS, ApplyResult, BriefPatch, apply_patch
from app.agent.hidden import NEEDS
from app.agent.planner import DEFAULT_NIGHTS, PlanResult, needs_summary, search_train_trip, search_trip, suggest
from app.agent.prompts import ASK_PROMPT, EXPLAIN_PROMPT, OFF_TOPIC_PROMPT, SUMMARY_PROMPT, context_message
from app.agent.router import Action, Decision, decide
from app.agent.session import Session, SessionStore, Stage
from app.llm import LLMClient, LLMError, Message
from app.tools import Tool, run_tool, tool_specs

log = logging.getLogger(__name__)

EventType = Literal[
    "status", "brief", "question", "tool_call", "tool_result", "options", "diff",
    "booking", "warning", "delta", "done", "error",
]  # fmt: skip

MAX_TOOL_STEPS = 3  # итераций модели с инструментами при ответе на вопрос
MAX_TOOL_CALLS = 6  # вызовов инструментов за ход в цикле модели


class AgentEvent(BaseModel):
    type: EventType
    data: dict[str, Any] = Field(default_factory=dict)


Emit = Callable[[EventType, dict[str, Any]], None]


class TravelAgent:
    def __init__(
        self,
        llm: LLMClient,
        tools: Mapping[str, Tool[Any, Any]],
        *,
        llm_writes_text: bool = True,
        sessions: SessionStore | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        """llm_writes_text=False — тексты по шаблонам (фейковый провайдер), разбор правилами."""
        self._llm = llm
        self._tools = tools
        self._llm_writes = llm_writes_text
        self.sessions = sessions or SessionStore()
        self._today = today
        self._locks: dict[str, asyncio.Lock] = {}

    async def run(self, message: str, session_id: str | None = None) -> AsyncIterator[AgentEvent]:
        stored = self.sessions.get_or_create(session_id)
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

        def emit(type_: EventType, data: dict[str, Any]) -> None:
            queue.put_nowait(AgentEvent(type=type_, data=data))

        async def turn() -> None:
            lock = self._locks.setdefault(stored.id, asyncio.Lock())
            try:
                async with lock:
                    work = copy.deepcopy(stored)  # ход меняет копию, сохраняем только успешный
                    await self._turn(work, message, emit)
                    stored.__dict__.update(work.__dict__)
                    emit("done", {"session_id": stored.id, "stage": stored.stage.value, "version": stored.version})
            except Exception:
                log.exception("Ход агента упал")
                emit("error", {"message": "Что-то пошло не так, условия поездки не изменились. Попробуйте ещё раз."})
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(turn())
        try:
            while (event := await queue.get()) is not None:
                yield event
        finally:
            if not task.done():  # клиент отключился посреди ответа
                task.cancel()

    # ======================================================================
    # Один ход
    # ======================================================================

    async def _turn(self, s: Session, text: str, emit: Emit) -> None:
        today = self._today()
        emit("status", {"stage": "analyze", "text": "Разбираю запрос"})
        outcome = await analyze_message(
            self._llm if self._llm_writes else None,
            text,
            brief=s.brief,
            history=s.history_tail(),
            today=today,
            has_options=bool(s.packages or s.destinations or s.trains),
            awaiting_confirmation=s.stage is Stage.AWAITING_CONFIRMATION,
        )
        for warning in outcome.warnings:
            emit("warning", {"message": warning})
        analysis = outcome.analysis
        self._select_destination(s, analysis)

        applied = apply_patch(s.brief, s.sources, analysis.patch, today=today)
        self._commit_brief(s, applied, emit)

        # Об отклонённых значениях говорим сразу и честно, а не теряем их молча.
        rejected = fmt.rejected_line(applied)
        if rejected:
            emit("delta", {"text": rejected + "\n"})

        decision = decide(s, analysis, search_changed=applied.search_changed)
        log.info("route: %s (%s)", decision.action.value, decision.reason)
        reply = await self._execute(s, decision, text, analysis, emit)
        if rejected:
            reply = f"{rejected}\n{reply}"
        s.history += [Message(role="user", content=text), Message(role="assistant", content=reply)]

    def _select_destination(self, s: Session, analysis: MessageAnalysis) -> None:
        """«Давай второй» после карточек направлений — это выбор направления, а не пакета."""
        if analysis.intent != "select" or s.stage is not Stage.DESTINATIONS_SHOWN or analysis.option is None:
            return
        if 1 <= analysis.option <= len(s.destinations):
            analysis.patch.destination = s.destinations[analysis.option - 1]["code"]
            analysis.intent = "plan"

    def _commit_brief(self, s: Session, applied: ApplyResult, emit: Emit, *, search: bool = True) -> None:
        if applied.changes:
            s.brief, s.sources = applied.brief, applied.sources
            s.version += 1
            if search and applied.search_changed:
                s.stale = bool(s.packages or s.destinations or s.trains)
                s.selected = None
                if s.stage in (Stage.AWAITING_CONFIRMATION, Stage.PACKAGES_SHOWN, Stage.BOOKED):
                    s.stage = Stage.PACKAGES_SHOWN if s.packages else Stage.COLLECTING
                if any(c.field in ("origin", "month") for c in applied.changes) and not s.brief.destination:
                    s.destinations = []
        if applied.changes or applied.rejected:
            emit(
                "brief",
                {
                    "brief": s.brief.model_dump(mode="json"),
                    "sources": s.sources,
                    "changes": [
                        {
                            "field": c.field,
                            "label": FIELD_LABELS.get(c.field, c.field),
                            "old": _plain(c.old),
                            "new": _plain(c.new),
                            "source": c.source,
                        }
                        for c in applied.changes
                    ],
                    "rejected": [{"field": r.field, "value": r.value, "reason": r.reason} for r in applied.rejected],
                    "needs": [
                        {"code": n, "label": NEEDS[n].label, "effect": NEEDS[n].effect}
                        for n in s.brief.needs
                        if n in NEEDS
                    ],
                },
            )

    async def _execute(self, s: Session, d: Decision, text: str, analysis: MessageAnalysis, emit: Emit) -> str:
        match d.action:
            case Action.ASK:
                return await self._ask(s, d, text, analysis, emit)
            case Action.SUGGEST:
                return await self._suggest(s, text, emit)
            case Action.SEARCH:
                return await self._search(s, text, emit)
            case Action.EXPLAIN:
                return await self._explain(s, text, emit)
            case Action.ASK_WHICH:
                return self._say(fmt.which_option(len(s.packages)), emit)
            case Action.CONFIRM:
                return self._request_confirmation(s, d, emit)
            case Action.BOOK:
                return self._book(s, emit)
            case Action.CANCEL:
                s.stage, s.selected = Stage.PACKAGES_SHOWN, None
                return self._say(fmt.cancelled(), emit)
            case Action.OFF_TOPIC:
                return await self._speak(
                    [Message(role="system", content=OFF_TOPIC_PROMPT), Message(role="user", content=text)],
                    fmt.off_topic(),
                    emit,
                )

    # ======================================================================
    # Действия
    # ======================================================================

    async def _ask(self, s: Session, d: Decision, text: str, analysis: MessageAnalysis, emit: Emit) -> str:
        field = d.field or ""
        today = self._today()
        fallback = fmt.question(field, s.brief)
        emit("question", {"field": field, "text": fallback, "quick_replies": fmt.quick_replies(field, today)})
        messages = [
            Message(role="system", content=ASK_PROMPT),
            context_message(
                "ask",
                today,
                question_field=field,
                question_label=FIELD_LABELS.get(field, field),
                brief=s.brief.model_dump(mode="json"),
                sources=s.sources,
                needs=needs_summary(s.brief),
                contradictions=analysis.contradictions,
            ),
            *s.history_tail(),
            Message(role="user", content=text),
        ]
        return await self._speak(messages, fallback, emit)

    async def _suggest(self, s: Session, text: str, emit: Emit) -> str:
        emit("status", {"stage": "suggest", "text": "Подбираю направления"})
        plan = await suggest(s.brief, self._caller(s, emit))
        s.destinations = plan.options
        s.trains = []
        s.packages, s.stale = [], False
        s.stage = Stage.DESTINATIONS_SHOWN if plan.options else Stage.COLLECTING
        self._emit_options("destinations", plan, emit)
        return await self._summarize(s, text, plan, fmt.destinations(plan), emit)

    async def _search(self, s: Session, text: str, emit: Emit) -> str:
        if s.brief.transport == "train":
            emit("status", {"stage": "search", "text": "Ищу поезда и считаю стоимость для группы"})
            plan = await search_train_trip(s.brief, self._caller(s, emit))
            s.trains, s.packages, s.destinations = plan.options, [], []
            s.stale, s.selected = False, None
            s.stage = Stage.TRAINS_SHOWN if plan.options else Stage.COLLECTING
            self._emit_options("trains", plan, emit)
            return await self._summarize(s, text, plan, fmt.trains(plan), emit)
        emit("status", {"stage": "search", "text": "Собираю поездку: рейсы, отели, трансферы"})
        before = s.packages[0] if s.packages and s.stale else None
        plan = await search_trip(s.brief, self._caller(s, emit), self._today())
        self._adopt_assumptions(s, plan, emit)
        s.packages, s.stale, s.selected = plan.options, False, None
        s.trains, s.destinations = [], []
        s.stage = Stage.PACKAGES_SHOWN if plan.options else Stage.COLLECTING
        self._emit_options("packages", plan, emit)
        diff = ""
        if before and plan.options:
            after = plan.options[0]
            emit("diff", {"before": _brief_package(before), "after": _brief_package(after)})
            delta = after["known_total_rub"] - before["known_total_rub"]
            sign = "+" if delta > 0 else "−"
            diff = (
                f"Пересчитал известные расходы. Было: {before['hotel']['name']}, "
                f"{fmt.rub(before['known_total_rub'])} → "
                f"стало: {after['hotel']['name']}, {fmt.rub(after['known_total_rub'])} ({sign}{fmt.rub(abs(delta))})."
            )
        return await self._summarize(s, text, plan, fmt.packages(plan, diff), emit, diff=diff)

    def _adopt_assumptions(self, s: Session, plan: PlanResult, emit: Emit) -> None:
        """Дату и срок, которые выбрал планировщик, кладём в бриф с источником default.

        Тогда «а если на два дня позже?» считается от реальной даты, а интерфейс показывает
        допущение плашкой, которую можно поправить.
        """
        patch = BriefPatch(
            start_date=plan.start_date.isoformat() if plan.start_date and s.brief.start_date is None else None,
            nights=DEFAULT_NIGHTS if s.brief.nights is None and plan.options else None,
        )
        if patch.is_empty():
            return
        applied = apply_patch(s.brief, s.sources, patch, today=self._today(), default_fields=frozenset(patch.values()))
        s.brief, s.sources = applied.brief, applied.sources
        self._commit_brief(s, ApplyResult(applied.brief, applied.sources, applied.changes), emit, search=False)

    async def _explain(self, s: Session, text: str, emit: Emit) -> str:
        options = s.packages or s.trains or s.destinations
        fallback = (
            fmt.packages(PlanResult(options=s.packages))
            if s.packages
            else fmt.trains(PlanResult(options=s.trains))
            if s.trains
            else fmt.destinations(PlanResult(options=s.destinations))
            if s.destinations
            else "Пока у меня нет данных, чтобы ответить точно."
        )
        if not self._llm_writes:
            return self._say(fallback, emit)
        emit("status", {"stage": "explain", "text": "Готовлю ответ"})
        messages = [
            Message(role="system", content=EXPLAIN_PROMPT),
            context_message(
                "explain", self._today(), brief=s.brief.model_dump(mode="json"), options=options, results_stale=s.stale
            ),
            *s.history_tail(),
            Message(role="user", content=text),
        ]
        answer = await self._tool_loop(s, messages, emit)
        return self._say(answer or fallback, emit)

    def _request_confirmation(self, s: Session, d: Decision, emit: Emit) -> str:
        package = s.package(d.option)
        if package is None:
            return self._say(fmt.which_option(len(s.packages)), emit)
        if package.get("can_confirm") is False:
            return self._say(
                "Подтверждение недоступно: цена или обязательные условия не проверены либо есть критические проблемы. "
                "Сначала нужно исправить вариант.",
                emit,
            )
        s.selected, s.stage = d.option, Stage.AWAITING_CONFIRMATION
        emit("status", {"stage": "confirm", "text": "Жду подтверждения"})
        return self._say(fmt.confirmation(package, s.brief), emit)

    def _book(self, s: Session, emit: Emit) -> str:
        package = s.package(s.selected)
        # Двойная проверка инварианта: покупка только после явного подтверждения выбранного варианта.
        if s.stage is not Stage.AWAITING_CONFIRMATION or package is None or package.get("can_confirm") is False:
            return self._say("Сначала выберите вариант — я покажу состав поездки и попрошу подтверждение.", emit)
        s.booking = {
            "id": "DEMO-" + uuid.uuid4().hex[:8].upper(),
            "option": s.selected,
            "hotel": package["hotel"]["name"],
            "total_rub": package["total_rub"],
            "package": package,
            "is_simulated": True,
        }
        s.stage = Stage.BOOKED
        emit("booking", {k: v for k, v in s.booking.items() if k != "package"})
        return self._say(fmt.booked(s.booking), emit)

    # ======================================================================
    # Инструменты
    # ======================================================================

    def _caller(self, s: Session, emit: Emit) -> Callable[[str, dict[str, Any]], Any]:
        async def call(name: str, arguments: dict[str, Any]) -> Any | None:
            emit("tool_call", {"name": name, "arguments": arguments})
            try:
                result = await run_tool(name, arguments, tools=self._tools)
            except KeyError:
                emit("tool_result", {"name": name, "ok": False, "error": "нет такого инструмента"})
                return None
            except ValidationError as exc:
                emit("tool_result", {"name": name, "ok": False, "error": _validation_message(exc)})
                return None
            except Exception as exc:  # неподдерживаемый маршрут, сбой источника — поиск продолжается
                log.warning("Инструмент %s упал: %s", name, exc)
                emit(
                    "tool_result",
                    {"name": name, "ok": False, "error": "Источник недоступен или вернул неподдерживаемые данные"},
                )
                return None
            emit("tool_result", {"name": name, "ok": True, "count": len(result) if isinstance(result, list) else 1})
            return result

        return call

    async def _tool_loop(self, s: Session, messages: list[Message], emit: Emit) -> str | None:
        """Модель отвечает на вопрос и при необходимости сама вызывает инструменты D1.

        Состав группы модель поменять не может: travelers всегда берётся из брифа.
        """
        call = self._caller(s, emit)
        specs = tool_specs(self._tools)
        calls = 0
        for _ in range(MAX_TOOL_STEPS):
            try:
                response = await self._llm.complete(messages, tools=specs)
            except LLMError as exc:
                emit("warning", {"message": f"Модель не ответила: {exc}"})
                return None
            if not response.tool_calls:
                return (response.text or "").strip() or None
            messages.append(Message(role="assistant", content=response.text, tool_calls=response.tool_calls))
            for tc in response.tool_calls:
                if calls >= MAX_TOOL_CALLS:
                    payload: Any = {"error": "лимит вызовов инструментов на этот ход исчерпан"}
                else:
                    calls += 1
                    args = dict(tc.arguments)
                    tool = self._tools.get(tc.name)
                    if tool is not None and "travelers" in tool.input_model.model_fields:
                        args["travelers"] = s.brief.travelers.model_dump()
                    result = await call(tc.name, args)
                    payload = _jsonable(result) if result is not None else {"error": "инструмент не ответил"}
                messages.append(
                    Message(role="tool", tool_call_id=tc.id, content=json.dumps(payload, ensure_ascii=False))
                )
        # Модель всё время звала инструменты: просим итог без них.
        messages.append(Message(role="user", content="Ответь пользователю по уже полученным данным."))
        try:
            response = await self._llm.complete(messages)
        except LLMError:
            return None
        return (response.text or "").strip() or None

    # ======================================================================
    # Тексты
    # ======================================================================

    async def _summarize(
        self, s: Session, text: str, plan: PlanResult, fallback: str, emit: Emit, diff: str = ""
    ) -> str:
        if not plan.options:
            return self._say(fallback, emit)
        messages = [
            Message(role="system", content=SUMMARY_PROMPT),
            context_message(
                "summary",
                self._today(),
                brief=s.brief.model_dump(mode="json"),
                sources=s.sources,
                needs=needs_summary(s.brief),
                assumptions=[a.text for a in plan.assumptions],
                notes=plan.notes,
                options=plan.options,
                rebuild=diff or None,
            ),
            *s.history_tail(),
            Message(role="user", content=text),
        ]
        return await self._speak(messages, fallback, emit)

    async def _speak(self, messages: list[Message], fallback: str, emit: Emit) -> str:
        """Потоковый ответ модели; если модель не ответила до первого слова — шаблон."""
        if not self._llm_writes:
            return self._say(fallback, emit)
        parts: list[str] = []
        try:
            async for chunk in self._llm.stream(messages):
                parts.append(chunk)
                emit("delta", {"text": chunk})
        except LLMError as exc:
            log.warning("Модель оборвала ответ: %s", exc)
            emit("warning", {"message": "Модель не ответила, показываю ответ по шаблону"})
            tail = ("\n\n" if parts else "") + fallback
            emit("delta", {"text": tail})
            parts.append(tail)
        text = "".join(parts).strip()
        return text or self._say(fallback, emit)

    def _say(self, text: str, emit: Emit) -> str:
        emit("delta", {"text": text})
        return text

    def _emit_options(self, kind: str, plan: PlanResult, emit: Emit) -> None:
        emit(
            "options",
            {
                "kind": kind,
                "items": plan.options,
                "assumptions": [{"field": a.field, "text": a.text} for a in plan.assumptions],
                "notes": plan.notes,
            },
        )


def _plain(value: Any) -> Any:
    """Значение поля брифа для JSON события: даты — строкой ISO."""
    return value.isoformat() if isinstance(value, date) else value


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())


def _brief_package(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": p["label"],
        "hotel": p["hotel"]["name"],
        "total_rub": p["total_rub"],
        "known_total_rub": p["known_total_rub"],
        "price_scope": p["price_scope"],
        "check_in": p["hotel"]["check_in"],
        "check_out": p["hotel"]["check_out"],
    }
