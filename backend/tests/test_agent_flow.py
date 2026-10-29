"""A2: полный путь агента — от размытого запроса до имитации покупки, пересборка и защита от ошибок модели."""

import json
from datetime import date
from typing import Any

import pytest

from app.agent import AgentEvent, Stage, TravelAgent
from app.agent import core as agent_core
from app.llm import FakeLLMClient, LLMError, LLMResponse, ToolCall
from app.tools import TOOLS

TODAY = date(2026, 10, 27)


class Turn:
    def __init__(self, events: list[AgentEvent]) -> None:
        self.events = events
        self.text = "".join(e.data["text"] for e in events if e.type == "delta")
        self.last = events[-1]

    def of(self, type_: str) -> list[dict[str, Any]]:
        return [e.data for e in self.events if e.type == type_]

    def tools(self) -> list[str]:
        return [e["name"] for e in self.of("tool_call")]


async def say(agent: TravelAgent, message: str, session_id: str | None = None) -> Turn:
    events = [event async for event in agent.run(message, session_id)]
    for event in events:
        json.dumps(event.data, ensure_ascii=False)  # так их отдаёт /api/chat: всё должно сериализоваться
    return Turn(events)


def offline_agent() -> TravelAgent:
    return TravelAgent(FakeLLMClient(), TOOLS, llm_writes_text=False, today=lambda: TODAY)


def model_agent(*responses: LLMResponse) -> tuple[TravelAgent, FakeLLMClient]:
    llm = FakeLLMClient(list(responses))
    return TravelAgent(llm, TOOLS, llm_writes_text=True, today=lambda: TODAY), llm


def as_json(**data: Any) -> LLMResponse:
    return LLMResponse(text=json.dumps(data, ensure_ascii=False), model="fake-fast")


def text(value: str) -> LLMResponse:
    return LLMResponse(text=value, model="fake-main")


async def test_vague_request_to_simulated_purchase() -> None:
    agent = offline_agent()

    t = await say(agent, "хочу куда-нибудь к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов")
    sid = t.last.data["session_id"]
    assert t.of("question")[0]["field"] == "origin"  # один вопрос, а не анкета
    assert t.of("question")[0]["quick_replies"]
    assert not t.tools()

    t = await say(agent, "из Москвы", sid)
    assert t.of("question")[0]["field"] == "children_ages"

    t = await say(agent, "ребёнку 5 лет", sid)
    assert t.tools() == ["suggest_destinations"]
    destinations = t.of("options")[0]
    assert destinations["kind"] == "destinations" and destinations["items"]
    assert all(item["water_temperature_c"] >= 24 for item in destinations["items"])  # «тёплое море»
    assert t.last.data["stage"] == Stage.DESTINATIONS_SHOWN

    t = await say(agent, "давай первый", sid)
    assert {"price_calendar", "search_flights", "search_hotels", "get_transfer"} <= set(t.tools())
    packages = t.of("options")[0]["items"]
    assert packages and all(p["kind"] == "package" for p in packages)
    assert all(p["hotel"]["adults_only"] is not True for p in packages)  # с ребёнком
    assert all(p["why"] or p["tradeoffs"] for p in packages)  # объяснение по каждому
    assert any(a["field"] == "start_date" for a in t.of("options")[0]["assumptions"])
    assert "Допущения" in t.text

    t = await say(agent, "беру второй", sid)
    assert t.last.data["stage"] == Stage.AWAITING_CONFIRMATION
    assert not t.of("booking")  # выбор — ещё не покупка
    assert "Оформляем?" in t.text

    t = await say(agent, "да, оформляй", sid)
    booking = t.of("booking")[0]
    assert booking["id"].startswith("DEMO-") and booking["is_simulated"] is True
    assert booking["total_rub"] == packages[1]["total_rub"]
    assert t.last.data["stage"] == Stage.BOOKED


async def test_no_purchase_without_choice_and_confirmation() -> None:
    agent = offline_agent()
    t = await say(agent, "из Москвы в Анталью в ноябре на неделю вдвоём")
    sid = t.last.data["session_id"]
    assert len(t.of("options")[0]["items"]) > 1

    t = await say(agent, "да, оформляй", sid)
    assert not t.of("booking")
    assert t.last.data["stage"] == Stage.PACKAGES_SHOWN


async def test_rebuild_keeps_brief_and_shows_diff() -> None:
    agent = offline_agent()
    t = await say(agent, "из Москвы в Анталью в ноябре на неделю вдвоём")
    sid = t.last.data["session_id"]
    first_date = agent.sessions.get(sid).brief.start_date

    t = await say(agent, "Перенеси на два дня позже", sid)
    session = agent.sessions.get(sid)
    assert session.brief.start_date == date.fromordinal(first_date.toordinal() + 2)
    assert session.brief.destination == "AYT" and session.brief.adults == 2  # не начали с нуля
    assert t.of("diff")
    assert "Было:" in t.text

    t = await say(agent, "с нами едет собака", sid)
    assert agent.sessions.get(sid).brief.pets == 1
    assert all(p["hotel"]["pets_allowed"] is not False for p in t.of("options")[0]["items"])


async def test_rejected_value_is_reported_and_brief_kept() -> None:
    agent = offline_agent()
    t = await say(agent, "из Москвы на Марс в ноябре")
    session = agent.sessions.get(t.last.data["session_id"])
    assert session.brief.origin == "MOW" and session.brief.month == 11


async def test_off_topic() -> None:
    t = await say(offline_agent(), "напиши стихотворение про кота")
    assert not t.tools()
    assert "поездк" in t.text


async def test_model_parses_and_writes_summary() -> None:
    agent, llm = model_agent(
        as_json(
            intent="plan",
            patch={
                "origin": "SVO",
                "destination": "MLE",
                "month": 12,
                "adults": 2,
                "needs_add": ["romantic"],
                "inferred": ["adults"],
            },
        ),
        text("Собрал три варианта на Мальдивах."),
    )
    t = await say(agent, "медовый месяц на Мальдивах в декабре из Шереметьево")
    brief = t.of("brief")[0]
    assert brief["brief"]["destination"] == "MLE" and brief["sources"]["adults"] == "inferred"
    assert t.text == "Собрал три варианта на Мальдивах."
    assert t.of("options")[0]["kind"] == "packages"
    summary_context = llm.calls[-1][1].content
    assert summary_context.startswith("CONTEXT_JSON:") and '"options"' in summary_context


async def test_invalid_model_json_falls_back_to_rules() -> None:
    agent, _ = model_agent(text("не json"), text('{"intent": "nonsense"}'))
    t = await say(agent, "хочу на море в ноябре из Москвы")
    assert t.of("warning") and all("модель" not in w["message"].casefold() for w in t.of("warning"))
    assert t.of("brief")[0]["brief"]["month"] == 11
    assert t.last.type == "done"


async def test_model_cannot_change_group_in_tool_loop() -> None:
    agent = offline_agent()
    t = await say(agent, "из Москвы в Дубай в ноябре на неделю вдвоём")
    sid = t.last.data["session_id"]

    llm = FakeLLMClient(
        [
            as_json(intent="question"),
            LLMResponse(
                text=None,
                model="fake-main",
                tool_calls=[
                    ToolCall(
                        id="1",
                        name="search_flights",
                        arguments={
                            "origin": "SVO",
                            "destination": "DXB",
                            "departure_date": "2026-11-20",
                            "travelers": {"adults": 9},
                        },
                    )
                ],
            ),
            text("20 ноября есть рейсы."),
        ]
    )
    agent._llm, agent._llm_writes = llm, True
    t = await say(agent, "а 20 ноября есть рейсы?", sid)
    assert t.of("tool_call")[0]["arguments"]["travelers"] == {"adults": 2, "children_ages": [], "pets": 0}
    assert t.text == "20 ноября есть рейсы."


async def test_failed_turn_keeps_session(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = offline_agent()
    t = await say(agent, "из Москвы в Дубай в ноябре")
    sid = t.last.data["session_id"]
    before = agent.sessions.get(sid).snapshot()

    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_core, "search_trip", broken)
    t = await say(agent, "с нами едет собака", sid)
    assert t.last.type == "error"
    assert agent.sessions.get(sid).snapshot() == before


async def test_rules_add_hidden_needs_model_missed() -> None:
    agent, _ = model_agent(
        as_json(intent="plan", patch={"origin": "MOW", "destination": "AYT", "month": 11}),
        text("Готово."),
    )
    t = await say(agent, "из Москвы в Анталью в ноябре, с нами едет собака")
    brief = t.of("brief")[0]
    assert brief["brief"]["pets"] == 1 and brief["sources"]["pets"] == "inferred"
    assert "pets" in brief["brief"]["needs"]


async def test_tool_loop_provider_diagnostics_never_reach_chat_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    private = "private-provider-token-and-traveler-data"

    class UnavailableModel(FakeLLMClient):
        async def complete(self, *args: Any, **kwargs: Any) -> LLMResponse:
            if kwargs.get("json_mode"):
                return as_json(intent="question")
            raise LLMError(private)

    agent = offline_agent()
    first = await say(agent, "из Москвы в Дубай в ноябре на неделю вдвоём")
    agent._llm, agent._llm_writes = UnavailableModel(), True
    result = await say(agent, "Какие есть условия перелёта?", first.last.data["session_id"])
    assert result.last.type == "done"
    assert result.of("warning")
    assert private not in json.dumps([event.data for event in result.events])
    assert private not in caplog.text
