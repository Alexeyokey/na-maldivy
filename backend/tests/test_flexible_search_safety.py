"""Adversarial checks of configured sources and bounded, read-only comparisons."""

import json
from datetime import date
from pathlib import Path

import pytest

from app.agent.brief import TripBrief
from app.agent.date_savings import compare_date_savings
from app.agent.destination_policy import DestinationPolicy
from app.agent.flexible_search import search_flexible_trip
from app.agent.session import Session, Stage
from app.submission_rehearsal import forbid_external_network
from app.three_destination_rehearsal import mixed_demo_settings
from app.tools import run_tool
from app.tools.destinations import load_catalogue
from app.tools.registry import create_tools

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 29)


@pytest.fixture
async def registry(tmp_path):
    settings = mixed_demo_settings(ROOT / "data/demo/aviasales", tmp_path / "checkout.sqlite3")
    tools = create_tools(settings)
    with forbid_external_network() as network:
        yield tools
        assert network["blocked_attempts"] == 0
    await tools.aclose()


def brief(**changes):
    return TripBrief.model_validate(
        {
            "origin": "MOW",
            "destination": "CMB",
            "adults": 2,
            "budget_rub": 200_000,
            "nights": 7,
            "needs": ["sea", "diving"],
            "min_water_temperature_c": 24,
            "departure_window_start": "2027-01-11",
            "departure_window_end": "2027-01-11",
            **changes,
        }
    )


def policy():
    return DestinationPolicy(data_path=ROOT / "data/destinations.yaml")


def caller(registry, calls, *, missing=None):
    async def call(name, arguments):
        # A search/comparison cannot reach a cart, booking or payment tool.
        assert name in {"price_calendar", "search_flights", "search_hotels", "get_transfer"}
        calls.append((name, dict(arguments)))
        return None if name == missing else await run_tool(name, arguments, tools=registry)

    return call


async def test_corrupt_configured_catalogue_fails_closed_without_fallback(tmp_path):
    path = tmp_path / "destinations.yaml"
    path.write_text("destinations: [ broken yaml", encoding="utf-8")

    async def forbidden_call(name, arguments):
        pytest.fail("A corrupt configured source must not select a global fallback or generate a quote")

    plan = await search_flexible_trip(
        brief(destination=None),
        forbidden_call,
        TODAY,
        supported_destinations=frozenset({"CMB"}),
        destination_policy=DestinationPolicy(data_path=path),
    )
    assert not plan.options and plan.notes


async def test_missing_hard_filter_source_cannot_generate_a_quote(registry):
    calls = []
    plan = await search_flexible_trip(
        brief(),
        caller(registry, calls),
        TODAY,
        supported_destinations=frozenset({"CMB"}),
        destination_policy=None,
    )
    assert not plan.options
    assert all(name == "price_calendar" for name, _ in calls)


async def test_visa_rule_starting_mid_month_does_not_discard_all_later_dates(registry):
    catalogue = load_catalogue(ROOT / "data/destinations.yaml")
    record = next(item for item in catalogue.destinations if item.iata == "CMB")
    visa = record.visa.model_copy(
        update={
            "regime": "exempt",
            "verification_status": "official_verified",
            "effective_from": date(2027, 1, 10),
            "effective_until": None,
            "checked_on": TODAY,
            "max_stay_days": 30,
        }
    )
    updated = catalogue.model_copy(
        update={
            "destinations": tuple(
                item.model_copy(update={"visa": visa}) if item.iata == "CMB" else item
                for item in catalogue.destinations
            )
        }
    )
    configured = DestinationPolicy(catalogue=updated, data_path=ROOT / "data/destinations.yaml")
    calls = []
    plan = await search_flexible_trip(
        brief(visa_free_only=True, departure_window_start="2027-01-01", departure_window_end="2027-01-31"),
        caller(registry, calls),
        TODAY,
        supported_destinations=frozenset({"CMB"}),
        destination_policy=configured,
    )
    assert plan.options
    assert all(package["outbound"]["departure_at"][:10] >= "2027-01-10" for package in plan.options)


async def test_one_activity_source_does_not_claim_another_activity(registry, tmp_path):
    source = json.loads((ROOT / "data/activity-references.json").read_text())
    source["destinations"] = {"CMB": source["destinations"]["CMB"]}
    source["destinations"]["CMB"]["activities"] = ["surfing"]
    (tmp_path / "activity-references.json").write_text(json.dumps(source), encoding="utf-8")
    configured = DestinationPolicy(
        catalogue=load_catalogue(ROOT / "data/destinations.yaml"), data_path=tmp_path / "destinations.yaml"
    )
    calls = []
    plan = await search_flexible_trip(
        brief(),
        caller(registry, calls),
        TODAY,
        supported_destinations=frozenset({"CMB"}),
        destination_policy=configured,
    )
    assert plan.options
    for package in plan.options:
        reference = package["activity_reference"]
        assert reference["destination_activities"] == ["surfing"]
        assert reference["hotel_availability_confirmed"] is reference["included_in_price"] is False
        assert not any("дайвинг описан" in line.casefold() for line in package["why"])


async def test_complete_group_quote_stays_below_hard_budget_and_never_pays(registry):
    calls = []
    plan = await search_flexible_trip(
        brief(),
        caller(registry, calls),
        TODAY,
        supported_destinations=frozenset({"CMB"}),
        destination_policy=policy(),
    )
    assert plan.options
    for package in plan.options:
        assert package["can_confirm"] and package["total_rub"] == package["known_total_rub"] <= 200_000
        assert package["travelers"] == {"adults": 2, "children_ages": [], "pets": 0}
        assert package["party_price_scope"] == "whole_party"
        assert "activities_and_equipment" in package["unpriced_components"]
    expensive = await search_flexible_trip(
        brief(budget_rub=1),
        caller(registry, calls),
        TODAY,
        supported_destinations=frozenset({"CMB"}),
        destination_policy=policy(),
    )
    assert not expensive.options


async def test_missing_return_flights_do_not_create_an_actionable_partial_price(registry):
    calls = []
    base = caller(registry, calls)

    async def no_return(name, arguments):
        if name == "search_flights" and arguments["origin"] == "CMB":
            calls.append((name, dict(arguments)))
            return None
        return await base(name, arguments)

    plan = await search_flexible_trip(
        brief(),
        no_return,
        TODAY,
        supported_destinations=frozenset({"CMB"}),
        destination_policy=policy(),
    )
    assert not plan.options and plan.notes


async def test_d8_for_last_card_cannot_leave_original_window_or_drop_unpriced_activities(registry):
    calls = []
    call = caller(registry, calls)
    request = brief()
    plan = await search_flexible_trip(
        request,
        call,
        TODAY,
        supported_destinations=frozenset({"CMB"}),
        destination_policy=policy(),
    )
    assert len(plan.options) >= 2
    chosen = plan.options[-1]
    session = Session(brief=request, packages=plan.options, stage=Stage.PACKAGES_SHOWN)
    before = session.snapshot()
    comparison = await compare_date_savings(
        session,
        call,
        today=TODAY,
        text=f"Для варианта {chosen['index']} сравни даты на 1–3 дня позже",
        destination_policy=policy(),
    )
    assert session.snapshot() == before
    facts = comparison.facts
    assert facts["baseline_option"] == chosen["index"]
    assert facts["baseline"]["hotel_id"] == chosen["hotel"]["hotel_id"]
    assert facts["baseline"]["destination"] == chosen["destination"]
    assert all(row["status"] == "outside_search_window" for row in facts["checked_dates"])
    assert not facts["suggestions"]
    assert set(chosen["unpriced_components"]) <= set(facts["unpriced_components"])
