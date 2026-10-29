"""Runs on the combined data branches; standalone orchestrator lacks the adapters.

No network, API keys or model responses are substituted for actual integration:
the six tools are constructed by create_tools(TOOLS_MODE=real) from repository data.
Only text generation is deliberately offline. This is not provider/live LLM QA.
"""

import json
from datetime import date, datetime
from importlib.util import find_spec
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.agent import TravelAgent
from app.agent.brief import TripBrief
from app.agent.planner import search_trip
from app.config import Settings
from app.llm import FakeLLMClient
from app.main import create_app
from app.tools import create_tools, run_tool

REQUIRED_ADAPTERS = ("d5_adapter", "flights", "hotels", "trains", "price_calendar", "hotel_profiles")
pytestmark = pytest.mark.skipif(
    any(find_spec(f"app.tools.{module}") is None for module in REQUIRED_ADAPTERS),
    reason="Requires data implementations from PRs #54-61 and registry #58; see docs/orchestrator-compatibility.md",
)
TODAY = date(2026, 10, 28)
DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture
def settings():
    return Settings(_env_file=None, tools_mode="real", llm_provider="fake", data_dir=DATA_ROOT)


async def chat(agent, text, sid=None):
    events = [e async for e in agent.run(text, sid)]
    json.dumps([e.model_dump() for e in events], ensure_ascii=False)
    assert events[-1].type == "done", events
    return events


def options(events, kind):
    return next(e.data["items"] for e in events if e.type == "options" and e.data["kind"] == kind)


async def test_real_nullable_d5_through_dialogue(settings):
    agent = TravelAgent(FakeLLMClient(), create_tools(settings), llm_writes_text=False, today=lambda: TODAY)
    events = await chat(agent, "Хочу тёплое море в ноябре из Москвы до 300к")
    rows = options(events, "destinations")
    assert rows and all(r["estimated_total_rub"] is None for r in rows)
    assert "цена неизвестна" in "".join(e.data["text"] for e in events if e.type == "delta")
    assert all(r["water_temperature_c"] >= 24 for r in rows)


@pytest.mark.parametrize("destination", ["AYT", "DXB", "MLE"])
async def test_real_packages_keep_profile_baggage_ids_and_validation(settings, destination):
    tools = create_tools(settings)

    async def call(name, arguments):
        return await run_tool(name, arguments, tools=tools)

    plan = await search_trip(
        TripBrief(
            origin="MOW",
            destination=destination,
            start_date=date(2026, 11, 18),
            nights=7,
            adults=2,
        ),
        call,
        TODAY,
    )
    assert plan.options
    for row in plan.options:
        assert row["hotel"]["hotel_id"].startswith("osm:") and row["transfer"] is not None
        assert row["hotel"]["review_profile"] is not None
        assert row["validation_status"] == "checked"
        expected = sum(row[k]["price_rub"] + row[k]["baggage_price_rub"] for k in ("outbound", "inbound"))
        assert row["total_rub"] == expected + row["hotel"]["price_rub"] + row["transfer"]["price_rub"]
        assert row["unpriced_components"]


async def test_real_seaplane_wait_reaches_s3_and_s5_instead_of_free_transfer(settings):
    tools = create_tools(settings)

    async def call(name, arguments):
        rows = await run_tool(name, arguments, tools=tools)
        if name == "search_flights" and arguments["destination"] == "MLE":
            row = rows[0].model_copy(
                update={
                    "departure_at": datetime(2026, 11, 18, 15, 0, tzinfo=ZoneInfo("Europe/Moscow")),
                    "arrival_at": datetime(2026, 11, 18, 23, 40, tzinfo=ZoneInfo("Indian/Maldives")),
                }
            )
            return [type(row).model_validate(row.model_dump())]
        if name == "search_hotels":
            return [h for h in rows if h.hotel_id == "osm:node/4455235191"]
        return rows

    plan = await search_trip(
        TripBrief(
            origin="MOW",
            destination="MLE",
            start_date=date(2026, 11, 18),
            nights=7,
        ),
        call,
        TODAY,
    )
    assert plan.options
    row = plan.options[0]
    assert row["transfer"]["available"] is False
    assert row["transfer"]["depart_at"].startswith("2026-11-19T06:00")
    issue = next(i for i in row["issues"] if i["rule"] == "airport_overnight_wait")
    assert issue["fixes"] and "patch" in issue["fixes"][0] and "price_estimated" in issue["fixes"][0]
    assert row["validation_status"] == "checked"


@pytest.mark.parametrize("origin,destination", [("LED", "MOW"), ("MOW", "LED")])
async def test_real_d6_via_orchestrator(settings, origin, destination):
    agent = TravelAgent(FakeLLMClient(), create_tools(settings), llm_writes_text=False, today=lambda: TODAY)
    events = await chat(agent, f"из {origin} в {destination} поездом 18 ноября 2026 плацкарт на двоих")
    rows = options(events, "trains")
    assert rows and all(row["seat_class"] == "platzkart" for row in rows)
    assert all(row["schedule_source"] == "synthetic" and row["schedule_cache_mode"] is None for row in rows)
    assert "Демонстрационное расписание" in "".join(e.data["text"] for e in events if e.type == "delta")
    assert {e.data["name"] for e in events if e.type == "tool_call"} == {"search_trains"}


def sse(response):
    assert response.status_code == 200 and "text/event-stream" in response.headers["content-type"]
    return [
        (lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: ")))
        for block in response.text.replace("\r\n", "\n").strip().split("\n\n")
        if (lines := block.splitlines()) and lines[0].startswith("event: ")
    ]


def test_http_real_registry_search_selection_confirmation_and_simulated_purchase(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.agent._today = lambda: TODAY
        first = sse(client.post("/api/chat", json={"message": "из Москвы в Дубай 18 ноября 2026 на неделю вдвоём"}))
        assert first[-1][0] == "done" and all(kind != "error" for kind, _ in first)
        sid = first[-1][1]["session_id"]
        rows = next(data["items"] for kind, data in first if kind == "options")
        chosen = next((r for r in rows if r["can_confirm"]), None)
        assert chosen is not None
        selected = sse(client.post("/api/chat", json={"message": f"беру {chosen['index']} вариант", "session_id": sid}))
        assert selected[-1][1]["stage"] == "awaiting_confirmation"
        assert not any(kind == "booking" for kind, _ in selected)
        confirmed = sse(client.post("/api/chat", json={"message": "да, оформляй", "session_id": sid}))
        booking = next(data for kind, data in confirmed if kind == "booking")
        assert booking["is_simulated"] is True and booking["total_rub"] == chosen["total_rub"]
        assert booking["status"] == "awaiting_travelers" and booking["paid"] is False
        assert client.get(f"/api/session/{sid}").json()["stage"] == "checkout_pending"


@pytest.mark.parametrize("destination", ["Дубай", "CMB"])
def test_http_real_registry_failure_does_not_fall_back_to_stub(settings, tmp_path, destination):
    settings.data_dir = tmp_path
    with TestClient(create_app(settings)) as client:
        client.app.state.agent._today = lambda: TODAY
        events = sse(client.post("/api/chat", json={"message": f"из Москвы в {destination} 18 ноября 2026 на неделю"}))
        assert events[-1][0] == "done"
        assert not next(data["items"] for kind, data in events if kind == "options")
        assert any(kind == "tool_result" and data["ok"] is False for kind, data in events)
        assert str(tmp_path) not in str(events)


async def test_d5_card_outside_seed_can_be_selected_without_rejecting_its_code(settings):
    agent = TravelAgent(FakeLLMClient(), create_tools(settings), llm_writes_text=False, today=lambda: TODAY)
    events = await chat(agent, "из Москвы в ноябре")
    sid = events[-1].data["session_id"]
    agent.sessions.get(sid).destinations = [{"index": 1, "code": "HKT"}]
    events = await chat(agent, "давай первый", sid)
    assert agent.sessions.get(sid).brief.destination == "HKT"
    assert not any(e.type == "brief" and e.data["rejected"] for e in events)
    assert not options(events, "destinations")  # a reference lookup, no invented packages beyond the seed routes
    assert any(e.type == "destination_info" and e.data["destination"] == "HKT" for e in events)
