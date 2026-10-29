"""A month is not an instruction to choose an ineligible cheap flight day."""

from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.brief import TripBrief
from app.agent.planner import PlanResult, _pick_start_date, search_trip
from app.chat_recovery import MAX_TURN_BYTES, MAX_TURN_EVENTS
from app.config import Settings
from app.contracts.tools import DayPrice
from app.main import create_app
from app.submission_rehearsal import forbid_external_network
from app.tools.registry import create_tools, run_tool
from app.tools.transport import ProviderResponseError
from app.u2_eval import parse_sse

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 29)


@pytest.fixture
async def source():
    settings = Settings(
        _env_file=None,
        llm_provider="fake",
        tools_mode="real",
        flights_mode="demo",
        trains_mode="synthetic",
        cache_mode="replay",
        aviasales_api_token=None,
        yandex_rasp_api_key=None,
        llm_api_key=None,
        data_dir=ROOT / "data",
        aviasales_cache_dir=ROOT / "data/demo/aviasales",
    )
    calls = []
    with forbid_external_network() as network:
        tools = create_tools(settings)

        async def call(name, arguments):
            calls.append((name, arguments))
            return await run_tool(name, arguments, tools=tools)

        try:
            yield call, calls
        finally:
            await tools.aclose()
        assert network["blocked_attempts"] == 0


@pytest.mark.parametrize("destination,month,limit", [("AYT", 3, 305), ("DXB", 1, 395)])
async def test_monthly_duration_search_finds_real_compatible_packages_instead_of_stopping_at_bad_cheapest_day(
    source, destination, month, limit
):
    call, calls = source
    brief = TripBrief(
        origin="MOW", destination=destination, month=month, year=2027, nights=7, adults=2, max_flight_minutes=limit
    )
    result = await search_trip(brief, call, TODAY)
    assert result.options and any(row["can_confirm"] for row in result.options)
    assert result.start_date.year == 2027 and result.start_date.month == month
    assert brief.start_date is None, "Do not mutate the user's month into an explicit date"
    for row in result.options:
        assert row["outbound"]["departure_at"].startswith(result.start_date.isoformat())
        for leg in ("outbound", "inbound"):
            from datetime import datetime

            duration = (
                datetime.fromisoformat(row[leg]["arrival_at"]) - datetime.fromisoformat(row[leg]["departure_at"])
            ).total_seconds() / 60
            assert duration <= limit
    dates = [args["departure_date"] for name, args in calls if name == "search_flights" and args["origin"] == "MOW"]
    assert len(dates) <= 31 and max(Counter(dates).values()) == 1
    assert not any(name == "price_calendar" for name, _ in calls)
    text = next(row.text for row in result.assumptions if row.field == "start_date")
    assert "по вашим условиям" in text and "минимум полной стоимости поездки не гарантируется" in text


async def test_monthly_direct_and_late_constraints_survive_all_legs_and_search_queries(source):
    call, calls = source
    brief = TripBrief(
        origin="MOW", destination="DXB", month=2, year=2027, nights=7, adults=2, direct_only=True, late_departure=True
    )
    result = await search_trip(brief, call, TODAY)
    assert result.options and any(row["can_confirm"] for row in result.options)
    for row in result.options:
        assert row["outbound"]["stops"] == row["inbound"]["stops"] == 0
        assert int(row["outbound"]["departure_at"][11:13]) >= 18 or int(row["outbound"]["departure_at"][11:13]) < 6
    assert all(args["direct_only"] is True for name, args in calls if name == "search_flights")
    assert "вечерний или ночной вылет туда" in result.assumptions[-1].text


async def test_impossible_monthly_flight_constraints_are_empty_and_bounded_without_hotel_or_return_work(source):
    call, calls = source
    result = await search_trip(
        TripBrief(origin="MOW", destination="AYT", month=2, year=2027, nights=7, adults=2, max_flight_minutes=1),
        call,
        TODAY,
    )
    assert not result.options and result.start_date is None and result.notes
    assert len(calls) == 28 and {name for name, _ in calls} == {"search_flights"}
    assert all(args["origin"] == "MOW" and args["destination"] == "AYT" for _, args in calls)


async def test_unconstrained_calendar_path_has_no_new_search_queries_and_explicit_date_never_changes():
    calls = []

    async def call(name, args):
        calls.append(name)
        return [DayPrice(date=date(2027, 2, 2), price_rub=100, is_synthetic=True)]

    brief = TripBrief(origin="MOW", destination="AYT", month=2, year=2027)
    result = PlanResult()
    assert await _pick_start_date(brief, call, TODAY, result) == date(2027, 2, 2)
    assert calls == ["price_calendar"]
    calls.clear()
    explicit = brief.model_copy(
        update={"start_date": date(2027, 2, 3), "direct_only": True, "late_departure": True, "max_flight_minutes": 1}
    )
    assert await _pick_start_date(explicit, call, TODAY, PlanResult()) == date(2027, 2, 3)
    assert calls == []


@pytest.mark.parametrize("failure", ["unavailable", "exception"])
async def test_monthly_source_errors_never_become_empty_days_or_trigger_another_date(source, failure):
    actual, calls = source
    attempted = []

    async def call(name, args):
        attempted.append(name)
        if name == "search_flights":
            if failure == "exception":
                raise ProviderResponseError("provider failed")
            return None
        return await actual(name, args)

    brief = TripBrief(origin="MOW", destination="AYT", month=2, year=2027, direct_only=True)
    if failure == "exception":
        with pytest.raises(ProviderResponseError):
            await search_trip(brief, call, TODAY)
    else:
        result = await search_trip(brief, call, TODAY)
        assert not result.options and "недоступен" in " ".join(result.notes)
    assert attempted == ["search_flights"]


async def test_return_source_failure_aborts_ranked_day_retry_instead_of_choosing_another_day(source):
    actual, calls = source

    async def call(name, args):
        if name == "search_flights" and args["origin"] == "AYT":
            calls.append((name, args))
            return None
        return await actual(name, args)

    brief = TripBrief(origin="MOW", destination="AYT", month=2, year=2027, direct_only=True)
    result = await search_trip(brief, call, TODAY)
    assert not result.options and "недоступен" in " ".join(result.notes)
    assert sum(name == "search_flights" and args["origin"] == "AYT" for name, args in calls) == 1


async def test_no_future_dates_in_a_month_does_not_query_or_loop(source):
    call, calls = source
    result = await search_trip(
        TripBrief(origin="MOW", destination="AYT", month=2, year=2026, direct_only=True), call, TODAY
    )
    assert not result.options and calls == []


def test_worst_monthly_budget_search_completes_and_replays_inside_real_http_recovery_limits(tmp_path):
    settings = Settings(
        _env_file=None,
        llm_provider="fake",
        tools_mode="real",
        flights_mode="demo",
        trains_mode="synthetic",
        cache_mode="replay",
        aviasales_api_token=None,
        yandex_rasp_api_key=None,
        llm_api_key=None,
        data_dir=ROOT / "data",
        aviasales_cache_dir=ROOT / "data/demo/aviasales",
        checkout_db_path=tmp_path / "checkout.sqlite3",
    )
    body = {
        "message": "Из Москвы в Анталью в феврале 2027 на неделю, 2 взрослых, только прямые рейсы, бюджет 1 рубль.",
        "request_id": "monthly-impossible-budget",
    }
    with forbid_external_network() as network, TestClient(create_app(settings)) as client:
        client.app.state.agent._today = lambda: TODAY
        response = client.post("/api/chat", json=body)
        assert response.status_code == 200
        events = parse_sse(response.text)
        assert events[-1]["type"] == "done" and not any(row["type"] == "error" for row in events)
        assert len(response.content) < MAX_TURN_BYTES and len(events) < MAX_TURN_EVENTS
        state = events[-1]["data"]
        assert state["brief"]["year"] == 2027 and state["brief"]["month"] == 2
        assert state["brief"]["budget_rub"] == 1 and state["brief"]["direct_only"] is True
        assert state["brief"]["adults"] == 2 and state["brief"]["nights"] == 7
        assert state["options"]["kind"] == "packages" and state["options"]["items"] == []
        assert state["stage"] == "collecting" and state["brief"]["start_date"] is None
        assert state["selected"] is state["booking"] is None
        assert client.get(f"/api/session/{state['session_id']}").json() == state
        # This must actually take the constrained month path, without a stale
        # implicit date or an unconstrained calendar shortening the traversal.
        outbound = [
            row["data"]["arguments"]["departure_date"]
            for row in events
            if row["type"] == "tool_call"
            and row["data"]["name"] == "search_flights"
            and row["data"]["arguments"]["origin"] == "MOW"
        ]
        assert len(outbound) == len(set(outbound)) == 28
        replay = client.post("/api/chat", json=body | {"retry": True})
        assert replay.status_code == 200 and replay.content == response.content
        assert client.get(f"/api/session/{state['session_id']}").json() == state
        assert network["blocked_attempts"] == 0
