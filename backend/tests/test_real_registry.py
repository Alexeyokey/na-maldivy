import builtins
import importlib.util
import shutil
from datetime import timedelta

import httpx
import pytest

from app.config import Settings
from app.data_paths import DEFAULT_DATA_DIR
from app.tools.registry import create_tools, run_tool, tool_specs
from app.tools.s4_adapter import transfer_leg

REAL_NAMES = {
    "search_flights",
    "search_hotels",
    "suggest_destinations",
    "get_transfer",
    "search_trains",
    "price_calendar",
}
ASSEMBLED = all(
    importlib.util.find_spec(name) is not None
    for name in ("app.tools.flights", "app.tools.hotels", "app.tools.trains", "app.tools.price_calendar")
)
assembled = pytest.mark.skipif(not ASSEMBLED, reason="Full registry requires D2/D3/D6/D8 implementation PRs")


def test_missing_implementation_is_explicit_and_never_falls_back_to_stubs(monkeypatch):
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "app.tools.flights":
            raise ModuleNotFoundError(name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(RuntimeError, match="requires the D2, D3, D5, D6 and D8"):
        create_tools(Settings(_env_file=None, tools_mode="real"))


def test_broken_dependency_is_not_hidden_as_an_unmerged_pr(monkeypatch):
    real_import = builtins.__import__

    def broken(name, *args, **kwargs):
        if name == "app.tools.flights":
            raise ModuleNotFoundError(name="broken_dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken)
    with pytest.raises(ModuleNotFoundError) as error:
        create_tools(Settings(_env_file=None, tools_mode="real"))
    assert error.value.name == "broken_dependency"


@assembled
@pytest.mark.parametrize("airport", ["AYT", "DXB", "MLE"])
async def test_public_dispatch_and_shared_hotel_identity_for_all_destinations(airport, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Default real registry attempted network access")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    tools = create_tools(Settings(_env_file=None, tools_mode="real"))
    assert set(tools) == REAL_NAMES
    assert {spec.name for spec in tool_specs(tools)} == REAL_NAMES
    assert all("stubs" not in type(tool).__module__ for tool in tools.values())
    flights = await run_tool(
        "search_flights",
        {
            "origin": "SVO",
            "destination": airport,
            "departure_date": "2026-11-18",
        },
        tools=tools,
    )
    hotels = await run_tool(
        "search_hotels",
        {
            "destination": airport,
            "check_in": "2026-11-18",
            "check_out": "2026-11-25",
        },
        tools=tools,
    )
    assert flights and hotels
    for hotel in hotels:
        quotes = await run_tool(
            "get_transfer",
            {
                "airport": airport,
                "hotel_id": hotel.hotel_id,
                "pickup_at": (flights[0].arrival_at + timedelta(minutes=45)).isoformat(),
            },
            tools=tools,
        )
        assert quotes and all(q.destination.id == hotel.hotel_id for q in quotes)
    destinations = await run_tool("suggest_destinations", {"origin": "SVO", "month": 11}, tools=tools)
    assert len(destinations) == 48
    assert all(item.estimated_total_price_rub is None for item in destinations)
    calendar = await run_tool(
        "price_calendar", {"origin": "SVO", "destination": airport, "month": "2026-11"}, tools=tools
    )
    assert len(calendar) == 30 and all(item.price_rub > 0 for item in calendar)
    trains = await run_tool(
        "search_trains", {"origin": "LED", "destination": "MOW", "departure_date": "2026-11-18"}, tools=tools
    )
    assert trains and trains[0].origin.kind == "city"
    with pytest.raises(KeyError):
        await run_tool("unsupported_tool", {}, tools=tools)


@assembled
async def test_same_settings_root_is_used_by_all_six_tools(tmp_path):
    shutil.copytree(DEFAULT_DATA_DIR, tmp_path / "data")
    settings = Settings(_env_file=None, tools_mode="real", data_dir=tmp_path / "data")
    tools = create_tools(settings)
    assert tools["search_flights"].routes_path == settings.data_dir / "routes/network.json"
    assert all(tools[name].data_dir == settings.data_dir for name in REAL_NAMES - {"search_flights", "price_calendar"})
    assert tools["price_calendar"].flights is tools["search_flights"]
    assert tools["search_trains"].model_path == settings.data_dir / "trains/model.json"
    assert await run_tool("suggest_destinations", {"origin": "LED", "month": 11}, tools=tools)


@assembled
async def test_real_generated_maldives_flight_reaches_s3_s5_overnight_warning():
    from app.booking.timeline import build_timeline
    from app.booking.validator import validate_trip
    from app.contracts.tools import Travelers
    from app.tools.transfers import TransferBase

    tools = create_tools(Settings(_env_file=None, tools_mode="real"))
    party = {"adults": 2}
    flights = await run_tool(
        "search_flights",
        {
            "origin": "SVO",
            "destination": "MLE",
            "departure_date": "2026-11-18",
            "travelers": party,
        },
        tools=tools,
    )
    flight = next(item for item in flights if item.departure_at.hour == 9)
    hotels = await run_tool(
        "search_hotels",
        {
            "destination": "MLE",
            "check_in": "2026-11-18",
            "check_out": "2026-11-25",
            "travelers": party,
        },
        tools=tools,
    )
    stay = next(item for item in hotels if item.hotel_id == "osm:node/4455235191")
    quotes = await run_tool(
        "get_transfer",
        {
            "airport": "MLE",
            "hotel_id": stay.hotel_id,
            "pickup_at": (flight.arrival_at + timedelta(minutes=45)).isoformat(),
            "travelers": party,
        },
        tools=tools,
    )
    quote = quotes[0]
    assert quote.mode == "seaplane" and quote.available is False
    assert quote.depart_at.hour == 6 and quote.depart_at.date() > flight.arrival_at.date()
    leg = transfer_leg(quote)
    timeline = build_timeline(flight, stay, arrival_transfer=leg)
    issues = validate_trip(
        timeline, stay, Travelers(**party), arrival_transfer=leg, airport="MLE", base=TransferBase.load()
    )
    assert any(item.rule == "airport_overnight_wait" and item.severity == "critical" for item in issues)
