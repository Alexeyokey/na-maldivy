"""Full hotel cards receive the actual nullable rules of the selected offer."""

from datetime import date, time

import pytest

from app.agent.brief import TripBrief
from app.agent.planner import search_trip
from app.tools.registry import run_tool
from app.tools.stubs import create_stub_tools


@pytest.mark.parametrize("scenario", ["unknown", "limited", "permitted"])
async def test_package_preserves_hotel_clock_rules_and_zero_price(scenario):
    tools = create_stub_tools()
    offers = {}
    rules = {
        "unknown": {
            "check_in_time": None,
            "check_out_time": None,
            "adults_only": None,
            "pets_allowed": None,
            "reception_24h": None,
            "reception_closes_at": None,
            "early_check_in_available": None,
            "early_check_in_price_rub": None,
        },
        "limited": {
            "check_in_time": time(14),
            "check_out_time": time(12, 30),
            "adults_only": True,
            "pets_allowed": False,
            "reception_24h": False,
            "reception_closes_at": time(23, 45),
            "early_check_in_available": False,
            "early_check_in_price_rub": None,
        },
        "permitted": {
            "check_in_time": time(0),
            "check_out_time": time(12),
            "adults_only": False,
            "pets_allowed": True,
            "reception_24h": True,
            "reception_closes_at": None,
            "early_check_in_available": True,
            "early_check_in_price_rub": 0,
        },
    }[scenario]

    async def call(name, arguments):
        result = await run_tool(name, arguments, tools=tools)
        if name == "search_hotels":
            result = [type(hotel).model_validate(hotel.model_dump() | rules) for hotel in result]
            offers.update({hotel.offer_id: hotel for hotel in result})
        return result

    result = await search_trip(
        TripBrief(origin="SVO", destination="DXB", start_date=date(2026, 11, 18), nights=7),
        call,
        date(2026, 10, 29),
    )
    assert result.options
    for option in result.options:
        hotel = option["hotel"]
        source = offers[hotel["offer_id"]]
        expected = source.model_dump(mode="json")
        for field in rules:
            assert hotel[field] == expected[field], field
        assert hotel["timezone"] == source.place.timezone
