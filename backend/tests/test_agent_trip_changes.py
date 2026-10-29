"""Explicit new trip requests supersede intent labels and never cancel paid history."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.agent import TravelAgent
from app.agent import formatting as fmt
from app.agent.planner import PlanResult
from app.agent.session import Stage
from app.booking.checkout_service import CheckoutService, DemoTraveler
from app.llm import FakeLLMClient, LLMResponse
from app.tools import TOOLS

TODAY = date(2026, 10, 29)


class MisclassifiedAnalysis(FakeLLMClient):
    def __init__(self, intent: str = "question", patch: dict[str, Any] | None = None):
        super().__init__()
        self.intent, self.patch = intent, patch or {}

    async def complete(self, messages: Any, **kwargs: Any) -> LLMResponse:
        self.calls.append(messages)
        if kwargs.get("json_mode"):
            return LLMResponse(text=json.dumps({"intent": self.intent, "patch": self.patch}), model="controlled")
        return LLMResponse(text="Проверенные условия поездки.", model="controlled")

    async def stream(self, messages: Any, **kwargs: Any):
        self.calls.append(messages)
        yield "Обновлённые варианты готовы."


async def say(agent: TravelAgent, message: str, sid: str | None = None):
    events = [event async for event in agent.run(message, sid)]
    assert events[-1].type == "done", events
    return events


def content(events: Any) -> str:
    return "".join(event.data["text"] for event in events if event.type == "delta")


async def prepared(tmp_path: Path, stage: str = "results"):
    checkout = CheckoutService(tmp_path / "checkout.sqlite3")
    agent = TravelAgent(FakeLLMClient(), TOOLS, llm_writes_text=False, today=lambda: TODAY, checkout=checkout)
    first = await say(agent, "Из Москвы на Мальдивы 18 ноября 2026 на неделю вдвоём, бюджет 400 тысяч")
    sid = first[-1].data["session_id"]
    session = agent.sessions.get(sid)
    order = None
    if stage in {"selected", "pending", "paid"}:
        chosen = next(option for option in session.packages if option["can_confirm"])
        await say(agent, f"беру {chosen['index']} вариант", sid)
    if stage in {"pending", "paid"}:
        confirmed = await say(agent, "да, оформляй", sid)
        cart = next(event.data for event in confirmed if event.type == "booking")["cart_id"]
        if stage == "pending":
            return agent, checkout, sid, None
        checkout.set_travelers(
            cart,
            session,
            [
                DemoTraveler(full_name="Demo Adult One", birth_date="1990-01-01"),
                DemoTraveler(full_name="Demo Adult Two", birth_date="1991-01-01"),
            ],
        )
        order = checkout.pay(
            cart,
            session,
            outcome="approved",
            idempotency_key="trip-change-approved",
            acknowledge_demo=True,
            acknowledge_unpriced=True,
        )
        session.stage = Stage.BOOKED
        # Keep the previous cart status intentionally stale: explanations must read SQLite.
        session.booking["order_id"] = order["order_id"]
    return agent, checkout, sid, order


@pytest.mark.parametrize("stage", ["results", "selected", "paid"])
@pytest.mark.parametrize("intent", ["question", "decline"])
async def test_exact_cancel_and_petersburg_request_replaces_results_without_cancelling_order(
    tmp_path: Path,
    stage: str,
    intent: str,
) -> None:
    agent, checkout, sid, order = await prepared(tmp_path, stage)
    before_documents = (
        (checkout.voucher(order["order_id"], sid), checkout.calendar(order["order_id"], sid)) if order else None
    )
    agent._llm, agent._llm_writes = MisclassifiedAnalysis(intent, {"destination": "MLE", "transport": "flight"}), True
    events = await say(agent, "Так а если отмена и я хочу в питер", sid)
    session = agent.sessions.get(sid)
    assert (session.brief.origin, session.brief.destination, session.brief.transport) == ("MOW", "LED", "train")
    assert session.stage is Stage.TRAINS_SHOWN
    assert session.trains and not session.packages and not session.destinations
    assert session.selected is None and session.booking is None
    changed = next(event.data for event in events if event.type == "brief")
    assert changed["search_changed"] is changed["invalidate_options"] is True
    assert changed["selected"] is changed["booking"] is None
    assert events[-1].data == session.snapshot()
    assert events[-1].data["options"]["kind"] == "trains"
    assert events[-1].data["options"]["items"] == session.trains
    if order:
        assert checkout.get_order(order["order_id"], sid)["status"] == "paid_demo"
        assert before_documents == (checkout.voucher(order["order_id"], sid), checkout.calendar(order["order_id"], sid))
        assert "сохранены" in content(events) and "возврат не выполнялись" in content(events)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("А я хочу теперь в Дубай", {"destination": "DXB"}),
        ("А давай 10 декабря 2026 на 5 ночей", {"start_date": date(2026, 12, 10), "nights": 5}),
        ("Поменяй бюджет на 160 тысяч", {"budget_rub": 160_000}),
        ("Теперь в декабре", {"month": 12}),
    ],
)
async def test_explicit_changes_rebuild_after_selection_even_if_intent_is_confirm(
    tmp_path: Path,
    message: str,
    expected: dict[str, Any],
) -> None:
    agent, _, sid, _ = await prepared(tmp_path, "selected")
    agent._llm, agent._llm_writes = MisclassifiedAnalysis("confirm"), True
    events = await say(agent, message, sid)
    session = agent.sessions.get(sid)
    assert all(getattr(session.brief, field) == value for field, value in expected.items())
    assert session.selected is session.booking is None
    assert not any(event.type == "booking" for event in events)
    assert any(event.type == "options" for event in events)
    assert next(event.data for event in events if event.type == "brief")["invalidate_options"] is True


async def test_composition_removal_is_authoritative_and_clears_hidden_needs(tmp_path: Path) -> None:
    agent = TravelAgent(FakeLLMClient(), TOOLS, llm_writes_text=False, today=lambda: TODAY)
    first = await say(agent, "Из Москвы в Анталью 18 ноября 2026 на неделю вдвоём с ребёнком 5 лет и собакой")
    sid = first[-1].data["session_id"]
    agent._llm, agent._llm_writes = MisclassifiedAnalysis(), True
    events = await say(agent, "Теперь еду один без детей и без собаки", sid)
    brief = agent.sessions.get(sid).brief
    assert (brief.adults, brief.children_ages, brief.pets) == (1, [], 0)
    assert not ({"child", "pets"} & set(brief.needs))
    assert next(event.data for event in events if event.type == "brief")["invalidate_options"] is True


@pytest.mark.parametrize("stage", ["selected", "paid"])
async def test_hypothetical_cancellation_preserves_selection_and_reports_actual_payment(
    tmp_path: Path,
    stage: str,
) -> None:
    agent, checkout, sid, order = await prepared(tmp_path, stage)
    session = agent.sessions.get(sid)
    before = (session.version, session.selected, session.stage, list(session.packages))
    agent._llm, agent._llm_writes = MisclassifiedAnalysis("decline", {"refundable_only": True}), True
    events = await say(agent, "А если отмена?", sid)
    assert (session.version, session.selected, session.stage, session.packages) == before
    assert not session.brief.refundable_only
    assert not any(event.type == "brief" and event.data["invalidate_options"] for event in events)
    assert "Отмена и возврат не выполнялись" in content(events)
    if order:
        assert "отмечен как оплаченный" in content(events) and "не завершена" not in content(events)
        assert session.booking["status"] == "paid_demo"
        assert checkout.get_order(order["order_id"], sid)["status"] == "paid_demo"


async def test_availability_question_does_not_move_current_departure(tmp_path: Path) -> None:
    agent, _, sid, _ = await prepared(tmp_path)
    session = agent.sessions.get(sid)
    original = session.brief.start_date
    agent._llm, agent._llm_writes = MisclassifiedAnalysis("question"), True
    await say(agent, "А 20 ноября есть рейсы?", sid)
    assert session.brief.start_date == original


async def test_unpaid_cart_change_does_not_claim_saved_order_documents(tmp_path: Path) -> None:
    agent, _, sid, _ = await prepared(tmp_path, "pending")
    events = await say(agent, "Хочу в Питер", sid)
    assert "оформление не было завершено" in content(events)
    assert "документы сохранены" not in content(events)


async def test_paid_question_uses_actual_checkout_status_instead_of_a_generated_claim(tmp_path: Path) -> None:
    agent, _, sid, _ = await prepared(tmp_path, "paid")
    agent._llm, agent._llm_writes = MisclassifiedAnalysis("question"), True
    events = await say(agent, "Оплачено ли уже?", sid)
    assert "отмечен как оплаченный" in content(events)
    assert "не завершена" not in content(events)


@pytest.mark.parametrize("destination", ["Питер", "СПб", "Санкт-Петербург"])
async def test_bare_domestic_destination_and_return_to_sea_are_supported(tmp_path: Path, destination: str) -> None:
    agent, _, sid, _ = await prepared(tmp_path)
    await say(agent, f"Хочу {destination}", sid)
    session = agent.sessions.get(sid)
    assert (session.brief.destination, session.brief.transport) == ("LED", "train")
    await say(agent, "Теперь хочу Дубай", sid)
    assert (session.brief.destination, session.brief.transport) == ("DXB", "flight")
    assert session.packages and not session.trains


async def test_compact_package_summary_keeps_prices_scope_and_unknown_extras(tmp_path: Path) -> None:
    agent, _, sid, _ = await prepared(tmp_path)
    options = agent.sessions.get(sid).packages
    summary = fmt.packages(PlanResult(options=options))
    lines = summary.splitlines()
    for option in options:
        start = next(index for index, line in enumerate(lines) if line.startswith(f"{option['index']}. "))
        assert lines[start + 1].startswith("   ") and lines[start + 2].startswith("   Ограничения:")
        assert fmt.rub(option["known_total_rub"]) in lines[start]
        assert start + 3 == len(lines) or not lines[start + 3].startswith("   ")
    assert "доплаты не оценены" in summary and "известные расходы" in summary.casefold()
    assert not any(word in summary.casefold() for word in ("модель", "aviasales", "api", "кэш"))


async def test_compact_summary_keeps_unconfirmed_hotel_rules_before_routine_warnings(tmp_path: Path) -> None:
    agent, _, sid, _ = await prepared(tmp_path)
    option = agent.sessions.get(sid).packages[0]
    option["travelers"] = {"adults": 1, "children_ages": [], "pets": 1}
    option["hotel"]["pets_allowed"] = None
    option["can_confirm"] = False
    option["issues"] = [
        {"severity": "warning", "message": "Трансфер требует уточнения"},
        {"severity": "warning", "message": "До заселения придётся подождать"},
    ]
    summary = fmt.packages(PlanResult(options=[option]))
    restrictions = next(line for line in summary.splitlines() if "Ограничения:" in line)
    assert "Отель не подтвердил размещение с питомцем — нужно уточнить до оплаты" in restrictions
    assert "Трансфер требует уточнения" in restrictions
    assert "До заселения придётся подождать" not in restrictions


@pytest.mark.parametrize("stage", ["selected", "paid"])
async def test_confirming_same_assumptions_updates_sources_without_losing_selection_or_order(
    tmp_path: Path, stage: str
) -> None:
    agent, _, sid, _ = await prepared(tmp_path, stage)
    session = agent.sessions.get(sid)
    session.sources.update(adults="default", nights="default")
    version, selected, current_stage, packages = (
        session.version,
        session.selected,
        session.stage,
        list(session.packages),
    )
    events = await say(agent, "Едет 2 взрослых на 7 ночей", sid)
    assert session.sources["adults"] == session.sources["nights"] == "explicit"
    assert session.version == version + 1
    assert (session.selected, session.stage, session.packages) == (selected, current_stage, packages)
    assert not any(event.type in {"tool_call", "options", "booking"} for event in events)
    changed = next(event.data for event in events if event.type == "brief")
    assert changed["changes"] == []
    assert changed["search_changed"] is changed["invalidate_options"] is False
    assert changed["sources"]["adults"] == changed["sources"]["nights"] == "explicit"
    assert events[-1].data == session.snapshot()
    if stage == "paid":
        assert session.booking["status"] == "paid_demo" and session.booking["order_id"]
