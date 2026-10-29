"""Hard climate/visa criteria apply to actual package dates and every D8 shift."""

import copy
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.brief import TripBrief
from app.agent.date_savings import compare_date_savings
from app.agent.destination_policy import DestinationPolicy, check_destination_policy, destination_policy_for_tools
from app.agent.planner import search_trip
from app.agent.session import Session, Stage
from app.config import Settings
from app.submission_rehearsal import forbid_external_network
from app.tools.d5_adapter import DestinationTool
from app.tools.destinations import Catalogue, load_catalogue
from app.tools.registry import create_tools, run_tool

DATA = Path(__file__).resolve().parents[2] / "data"
CATALOGUE = load_catalogue(DATA / "destinations.yaml")
POLICY = DestinationPolicy(catalogue=CATALOGUE)
TODAY = date(2026, 10, 29)


@pytest.mark.parametrize(
    "destination,departure,end,filters,passes",
    [
        ("KZN", "2027-07-18", "2027-07-25", {"min_water_temperature_c": 24}, False),
        ("AYT", "2027-01-18", "2027-01-25", {"min_water_temperature_c": 35}, False),
        ("AYT", "2027-07-18", "2027-07-25", {"min_water_temperature_c": 24}, True),
        ("NRT", "2027-07-18", "2027-07-25", {"visa_free_only": True}, False),
        ("CMB", "2027-07-18", "2027-07-25", {"visa_free_only": True}, False),
        ("KZN", "2027-07-18", "2027-07-25", {"visa_free_only": True}, True),
        ("PEK", "2027-12-28", "2028-01-04", {"visa_free_only": True}, False),
        ("PEK", "2027-12-24", "2027-12-31", {"visa_free_only": True}, True),
        ("ZZZ", "2027-07-18", "2027-07-25", {"visa_free_only": True}, False),
    ],
)
def test_real_d5_climate_and_visa_records_use_actual_departure_and_entire_trip(
    destination, departure, end, filters, passes
):
    brief = TripBrief(origin="MOW", destination=destination, **filters)
    notes = POLICY.check(brief, date.fromisoformat(departure), date.fromisoformat(end))
    assert (notes == []) is passes
    if not passes:
        assert notes and all(isinstance(note, str) and note for note in notes)


def changed_visa(**values):
    raw = copy.deepcopy(CATALOGUE.model_dump(mode="json"))
    record = next(row for row in raw["destinations"] if row["iata"] == "DXB")
    record["visa"].update(values)
    return DestinationPolicy(catalogue=Catalogue.model_validate(raw))


@pytest.mark.parametrize(
    "values",
    [
        {"verification_status": "partial"},
        {"max_stay_days": None},
        {"effective_from": "2027-07-19"},
        {"effective_until": "2027-07-24"},
        {"max_stay_days": 7},  # 7 nights plus both entry/exit days exceed a seven-day allowance.
    ],
)
def test_unverified_unknown_expired_not_yet_effective_and_too_short_rules_fail_closed(values):
    brief = TripBrief(origin="MOW", destination="DXB", visa_free_only=True)
    assert changed_visa(**values).check(brief, date(2027, 7, 18), date(2027, 7, 25))


def test_factory_uses_only_the_configured_d5_source_and_never_ambient_data(tmp_path):
    brief = TripBrief(origin="MOW", destination="AYT", min_water_temperature_c=24)
    configured = DestinationTool(data_dir=tmp_path)
    missing = destination_policy_for_tools({"suggest_destinations": configured})
    assert missing.check(brief, date(2027, 7, 18), date(2027, 7, 25))
    injected = destination_policy_for_tools(
        {"suggest_destinations": DestinationTool(data_dir=tmp_path, catalogue=CATALOGUE)}
    )
    assert injected.check(brief, date(2027, 7, 18), date(2027, 7, 25)) == []
    invalid = destination_policy_for_tools({"suggest_destinations": SimpleNamespace(catalogue={}, data_dir=DATA)})
    assert invalid.check(brief, date(2027, 7, 18), date(2027, 7, 25))
    assert destination_policy_for_tools({}) is None
    assert destination_policy_for_tools({"suggest_destinations": SimpleNamespace()}) is None
    assert check_destination_policy(None, brief, date(2027, 7, 18), date(2027, 7, 25))
    unrestricted = brief.model_copy(update={"min_water_temperature_c": None, "needs": ["sea", "warm"]})
    assert check_destination_policy(None, unrestricted, date(2027, 7, 18), date(2027, 7, 25)) == []
    assert (
        DestinationPolicy(data_path=tmp_path / "missing.yaml").check(unrestricted, date(2027, 7, 18), date(2027, 7, 25))
        == []
    )


def test_malformed_configured_yaml_is_an_explanatory_source_failure_without_fallback(tmp_path):
    malformed = tmp_path / "destinations.yaml"
    malformed.write_text("destinations: [not valid yaml", encoding="utf-8")
    policy = destination_policy_for_tools({"suggest_destinations": DestinationTool(data_dir=tmp_path)})
    notes = policy.check(
        TripBrief(origin="MOW", destination="AYT", min_water_temperature_c=24), date(2027, 7, 18), date(2027, 7, 25)
    )
    assert notes and "Источник" in notes[0] and "недоступен" in notes[0]
    assert str(tmp_path) not in " ".join(notes)


@pytest.fixture
async def sources():
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
        data_dir=DATA,
        aviasales_cache_dir=DATA / "demo/aviasales",
    )
    calls = []
    with forbid_external_network() as network:
        tools = create_tools(settings)

        async def call(name, arguments):
            calls.append((name, copy.deepcopy(arguments)))
            return await run_tool(name, arguments, tools=tools)

        try:
            yield call, calls, destination_policy_for_tools(tools)
        finally:
            await tools.aclose()
        assert network["blocked_attempts"] == 0


@pytest.mark.parametrize(
    "destination,start,filters",
    [
        ("KZN", "2027-07-18", {"min_water_temperature_c": 24}),
        ("AYT", "2027-01-18", {"min_water_temperature_c": 35}),
        ("NRT", "2027-07-18", {"visa_free_only": True}),
        ("CMB", "2027-07-18", {"visa_free_only": True}),
        ("PEK", "2027-12-28", {"visa_free_only": True}),
    ],
)
async def test_explicit_destination_cannot_bypass_sourced_hard_filters(sources, destination, start, filters):
    call, calls, policy = sources
    brief = TripBrief(origin="MOW", destination=destination, start_date=start, nights=7, adults=2, **filters)
    plan = await search_trip(brief, call, TODAY, destination_policy=policy)
    assert not plan.options and plan.notes
    assert calls == [], "A known mandatory constraint failure needs no invented travel search"


@pytest.mark.parametrize(
    "destination,filters", [("KZN", {"visa_free_only": True}), ("AYT", {"min_water_temperature_c": 24})]
)
async def test_compatible_domestic_and_warm_packages_still_search_and_report_reference_limits(
    sources, destination, filters
):
    call, calls, policy = sources
    brief = TripBrief(origin="MOW", destination=destination, start_date="2027-07-18", nights=7, adults=2, **filters)
    plan = await search_trip(brief, call, TODAY, destination_policy=policy)
    assert plan.options and any(option["can_confirm"] for option in plan.options)
    assert calls
    assert all(any("сохранённой справкой" in risk for risk in option["risks"]) for option in plan.options)


async def test_night_arrival_cannot_extend_hotel_and_return_beyond_the_recorded_visa_rule(sources):
    call, calls, policy = sources
    brief = TripBrief(
        origin="MOW",
        destination="PEK",
        start_date="2027-12-24",
        nights=7,
        adults=2,
        visa_free_only=True,
        late_departure=True,
    )
    # A naive start + 7 check passes, while the overnight flight requires return on January 1.
    assert policy.check(brief, date(2027, 12, 24), date(2027, 12, 31)) == []
    plan = await search_trip(brief, call, TODAY, destination_policy=policy)
    assert not plan.options and "01.01.2028" in " ".join(plan.notes)
    assert {name for name, _ in calls} == {"search_flights"}
    assert all(args["origin"] == "MOW" for _, args in calls)


async def test_constrained_month_retries_only_dates_whose_actual_return_stays_in_the_visa_period(sources):
    call, calls, policy = sources
    brief = TripBrief(
        origin="MOW",
        destination="PEK",
        month=12,
        year=2027,
        nights=7,
        adults=2,
        visa_free_only=True,
        late_departure=True,
    )
    plan = await search_trip(brief, call, TODAY, destination_policy=policy)
    assert plan.options and any(option["can_confirm"] for option in plan.options)
    assert plan.start_date <= date(2027, 12, 23)
    assert all(date.fromisoformat(option["hotel"]["check_out"]) <= date(2027, 12, 31) for option in plan.options)
    assert brief.start_date is None
    assert "безвизовый режим по сохранённой справке" in plan.assumptions[-1].text


async def test_quoted_hotel_and_return_dates_cannot_bypass_the_policy_by_disagreeing_with_requested_dates(sources):
    from datetime import timedelta

    actual, _, policy = sources

    async def extended_hotel(name, args):
        rows = await actual(name, args)
        if name == "search_hotels":
            return [row.model_copy(update={"check_out": row.check_out + timedelta(days=1)}) for row in rows]
        return rows

    brief = TripBrief(origin="MOW", destination="PEK", start_date="2027-12-24", nights=7, visa_free_only=True)
    result = await search_trip(brief, extended_hotel, TODAY, destination_policy=policy)
    assert not result.options and "01.01.2028" in " ".join(result.notes)


async def test_active_filters_without_policy_fail_before_any_quote_and_no_filters_keep_legacy_behavior(sources):
    call, calls, _ = sources
    brief = TripBrief(origin="MOW", destination="AYT", start_date="2027-07-18", nights=7, min_water_temperature_c=24)
    blocked = await search_trip(brief, call, TODAY)
    assert not blocked.options and blocked.notes and calls == []
    allowed = await search_trip(brief.model_copy(update={"min_water_temperature_c": None}), call, TODAY)
    assert allowed.options and calls


async def test_d8_will_not_recommend_a_cheaper_shift_whose_night_flight_expires_the_visa_rule(sources):
    call, calls, policy = sources
    brief = TripBrief(
        origin="MOW",
        destination="PEK",
        start_date="2027-12-23",
        nights=7,
        adults=2,
        visa_free_only=True,
        late_departure=True,
    )
    plan = await search_trip(brief, call, TODAY, destination_policy=policy)
    assert plan.options
    session = Session(brief=brief, packages=plan.options, stage=Stage.PACKAGES_SHOWN)
    package = session.packages[0]
    package["hotel"]["price_rub"] += 1_000_000
    package["total_rub"] += 1_000_000
    package["known_total_rub"] += 1_000_000
    before = copy.deepcopy(session.__dict__)
    calls.clear()
    comparison = await compare_date_savings(
        session, call, today=TODAY, text="А если на день позже, будет дешевле?", destination_policy=policy
    )
    assert comparison.facts["suggestions"] == []
    checked = comparison.facts["checked_dates"]
    assert len(checked) == 1 and checked[0]["status"] == "destination_constraints_not_met"
    assert "01.01.2028" in " ".join(checked[0]["notes"])
    assert {name for name, _ in calls} == {"search_flights"}
    assert all(args["origin"] == "MOW" for _, args in calls)
    assert session.__dict__ == before


async def test_d8_uses_shifted_departure_month_for_water_and_requires_policy(sources):
    call, calls, policy = sources
    brief = TripBrief(
        origin="MOW", destination="AYT", start_date="2027-06-01", nights=7, adults=2, min_water_temperature_c=24
    )
    # The source uses actual month, even though the unchanged brief continues to say June.
    assert policy.check(brief, date(2027, 6, 1), date(2027, 6, 8)) == []
    plan = await search_trip(brief, call, TODAY, destination_policy=policy)
    assert plan.options
    session = Session(brief=brief, packages=plan.options, stage=Stage.PACKAGES_SHOWN)
    calls.clear()
    missing = await compare_date_savings(session, call, today=TODAY)
    assert missing.facts["reason"] == "destination_constraints_not_met" and calls == []
    result = await compare_date_savings(
        session, call, today=TODAY, text="Если на день раньше, будет дешевле?", destination_policy=policy
    )
    assert result.facts["suggestions"] == []
    assert result.facts["checked_dates"][0]["status"] == "destination_constraints_not_met"
    assert "месяце вылета 05" in result.text and calls == []
