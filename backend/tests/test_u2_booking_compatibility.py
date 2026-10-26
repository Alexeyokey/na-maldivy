"""Real U2 outputs consumed by D1/S4, the S3 timeline and S5 rules, offline."""

import json
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.booking.timeline import AIRPORT_EXIT_MIN, build_timeline
from app.booking.validator import Requirements, validate_trip
from app.config import Settings
from app.contracts.tools import FlightSearch, HotelOffer, HotelSearch, TransferRequest, Travelers
from app.tools.data_services import open_data_services
from app.tools.errors import UnsupportedTransfer
from app.tools.flights import FlightTool
from app.tools.hotels import DATA_ROOT, HotelSearchTool, get_synthetic_rules, load_hotel_catalog
from app.tools.registry import run_tool
from app.tools.s4_adapter import S4TransferTool, transfer_leg
from app.tools.transfers import TransferBase

# Canonical S4 facts also drive D3; assertions below prevent vacuous or
# destination-limited parametrization if the common snapshot changes.
S4_HOTELS = json.loads((DATA_ROOT / "hotels/osm_hotels.json").read_text())["hotels"]
COMMON_HOTELS = tuple((hotel["destination"], hotel["hotel_id"]) for hotel in S4_HOTELS)
JEN = "osm:node/11182077737"


async def offered_stay(airport: str, hotel_id: str, party: Travelers) -> HotelOffer:
    # Demo inventory is date-dependent. Resolve a genuine offer rather than
    # constructing an offer from source records or bypassing its hard filters.
    for offset in range(14):
        day = date(2026, 11, 18) + timedelta(days=offset)
        offers = await HotelSearchTool().run(
            HotelSearch(destination=airport, check_in=day, check_out=day + timedelta(days=7), travelers=party)
        )
        selected = next((offer for offer in offers if offer.hotel_id == hotel_id), None)
        if selected is not None:
            return selected
    pytest.fail(f"No deterministic U2 offer found for {hotel_id}")


async def outbound_for(stay: HotelOffer, party: Travelers, departure_hour: int = 9):
    flights = await FlightTool().run(
        FlightSearch(origin="SVO", destination=stay.place.iata, departure_date=stay.check_in, travelers=party)
    )
    return next(flight for flight in flights if flight.departure_at.hour == departure_hour)


async def transfer_quotes(stay: HotelOffer, flight, party: Travelers):
    tool = S4TransferTool()
    return await run_tool(
        tool.name,
        {
            "airport": flight.destination.iata,
            "hotel_id": stay.hotel_id,
            "pickup_at": (flight.arrival_at + timedelta(minutes=AIRPORT_EXIT_MIN)).astimezone(UTC),
            "travelers": party.model_dump(),
        },
        tools={tool.name: tool},
    )


def test_d3_and_s4_catalogues_match_all_sixty_hotel_facts_and_routes():
    u2 = {hotel.hotel_id: hotel for hotel in load_hotel_catalog()}
    s4 = {hotel["hotel_id"]: hotel for hotel in S4_HOTELS}
    routes = {(record.hotel_id, record.airport) for record in TransferBase.load()}
    assert len(u2) == len(s4) == 60
    assert Counter(hotel.destination for hotel in u2.values()) == {"AYT": 20, "DXB": 20, "MLE": 20}
    assert set(u2) == set(s4)
    assert routes == {(record.hotel_id, record.destination) for record in u2.values()}
    for hotel_id, record in u2.items():
        source = s4[hotel_id]
        assert record.destination == source["destination"]
        assert record.name == source["name"]
        assert record.name_en == source["name_en"]
        assert (record.latitude, record.longitude) == (source["lat"], source["lon"])
        assert record.stars == source["stars"]
        assert record.source_url == source["osm_url"]


@pytest.mark.parametrize("airport,hotel_id", COMMON_HOTELS, ids=[hotel_id for _, hotel_id in COMMON_HOTELS])
@pytest.mark.parametrize("departure_hour", [9, 23])
async def test_every_common_u2_offer_reaches_s3_and_enforces_s5_transfer_limit(airport, hotel_id, departure_hour):
    party = Travelers(adults=2)
    stay = await offered_stay(airport, hotel_id, party)
    flight = await outbound_for(stay, party, departure_hour)
    quotes = await transfer_quotes(stay, flight, party)
    quote = quotes[0]
    leg = transfer_leg(quote)
    timeline = build_timeline(flight, stay, arrival_transfer=leg)
    issues = validate_trip(
        timeline,
        stay,
        party,
        arrival_transfer=leg,
        requirements=Requirements(max_transfer_minutes=1),
        airport=airport,
        base=TransferBase.load(),
    )
    by_rule = {issue.rule: issue for issue in issues}

    assert stay.hotel_id == stay.place.id == quote.destination.id == hotel_id
    assert stay.offer_id != stay.hotel_id
    assert stay.is_synthetic and flight.is_synthetic and all(item.is_synthetic for item in quotes)
    ready = flight.arrival_at + timedelta(minutes=AIRPORT_EXIT_MIN)
    assert quote.pickup_at.astimezone(UTC) == ready.astimezone(UTC)
    assert quote.pickup_at.utcoffset() == flight.arrival_at.utcoffset()
    assert quote.destination.timezone == stay.place.timezone
    assert quote.destination.name == stay.place.name
    assert (quote.destination.latitude, quote.destination.longitude) == (
        stay.place.latitude,
        stay.place.longitude,
    )
    assert len(quotes) == len(TransferBase.load().get(hotel_id, airport).options)
    assert leg.price_rub == quote.price_rub
    assert leg.issues == quote.issues
    assert leg.arrive_at.timestamp() - leg.depart_at.timestamp() == quote.duration_minutes * 60
    assert timeline.event("outbound_arrival").at == flight.arrival_at
    assert timeline.event("arrival_transfer_arrival").at == leg.arrive_at
    assert timeline.hotel_arrival_at == leg.arrive_at
    assert timeline.event("check_in").ref == hotel_id
    assert timeline.booked_check_in.date() == stay.check_in
    assert timeline.booked_check_out.date() == stay.check_out
    assert "hotel_times_unknown" not in by_rule
    assert "transfer_before_arrival" not in by_rule
    assert by_rule["long_transfer"].severity == "critical"
    assert by_rule["long_transfer"].fixes[0].patch == {"kind": "replace_hotel", "max_transfer_minutes": 1}
    assert flight.arrival_at.date() == stay.check_in + timedelta(days=int(departure_hour == 23))
    # Evening arrivals can reach an island the next morning even after the
    # daytime flight; paid-night rules depend on the actual S4 arrival.
    if leg.arrive_at.date() > stay.check_in:
        first_night = by_rule["wasted_first_night"]
        assert first_night.severity == ("info" if leg.arrive_at.time() < stay.check_in_time else "warning")
        assert first_night.fixes[0].price_estimated
        assert -stay.price_rub < first_night.fixes[0].price_delta_rub < 0
    else:
        assert "wasted_first_night" not in by_rule
    if quote.available:
        assert leg.depart_at == quote.pickup_at
        assert timeline.wait("airport_after_arrival") is None
    else:
        wait = timeline.wait("airport_after_arrival")
        assert "unavailable_at_pickup" in quote.issues
        assert wait is not None and wait.start == quote.pickup_at and wait.end == leg.depart_at


async def test_family_pet_group_prices_and_unknown_transfer_constraints_reach_s5():
    party = Travelers(adults=2, children_ages=[1, 5, 12], pets=1)
    stay = await offered_stay("MLE", JEN, party)
    single_party = Travelers()
    single = next(
        offer
        for offer in await HotelSearchTool().run(
            HotelSearch(destination="MLE", check_in=stay.check_in, check_out=stay.check_out, travelers=single_party)
        )
        if offer.hotel_id == JEN
    )
    flight = await outbound_for(stay, party, departure_hour=23)
    single_flight = await outbound_for(single, single_party, departure_hour=23)
    quotes = await transfer_quotes(stay, flight, party)
    quote = quotes[0]
    leg = transfer_leg(quote)
    timeline = build_timeline(flight, stay, arrival_transfer=leg)
    issues = validate_trip(
        timeline,
        stay,
        party,
        arrival_transfer=leg,
        requirements=Requirements(refundable_required=True),
        airport="MLE",
        base=TransferBase.load(),
    )
    by_rule = {issue.rule: issue for issue in issues}

    # Five people occupy three rooms; the pet fee is once for the whole stay.
    assert party.count == 5
    assert stay.price_rub == single.price_rub * 3 + 1000
    assert stay.hotel_id == single.hotel_id and stay.offer_id != single.offer_id
    assert flight.price_rub == single_flight.price_rub * 5 + 1500
    assert flight.baggage_price_rub == 10000
    # S4 already quotes two vehicles for the whole party, without a pet fee.
    assert quote.vehicles == 2
    assert quote.price_rub == leg.price_rub == 2200
    assert {"extra_vehicle", "pets_unknown", "child_seat_unknown"}.issubset(quote.issues)
    assert by_rule["transfer_pets"].severity == "warning"
    assert by_rule["transfer_child_seat"].severity == "warning"
    assert by_rule["nonrefundable_rate"].severity == "critical"
    assert "adults_only_with_children" not in by_rule
    assert "pets_not_allowed" not in by_rule
    for rule in ("transfer_pets", "transfer_child_seat"):
        assert by_rule[rule].fixes[0].price_delta_rub is None
    first_night = by_rule["wasted_first_night"].fixes[0]
    assert first_night.price_estimated
    nights = (stay.check_out - stay.check_in).days
    # The estimated saving is one group night, rounded to hundreds of RUB;
    # neither the room count nor the traveler count is multiplied a second time.
    assert abs(first_night.price_delta_rub + stay.price_rub / nights) <= 50

    request = TransferRequest(
        airport="MLE", hotel_id=stay.hotel_id, pickup_at=quote.pickup_at.astimezone(UTC), travelers=party
    )
    local = await S4TransferTool().run(request)
    moscow = await S4TransferTool().run(
        TransferRequest.model_validate(
            request.model_dump() | {"pickup_at": quote.pickup_at.astimezone(flight.departure_at.tzinfo)}
        )
    )
    assert local == moscow == quotes


@pytest.mark.parametrize("missing", [("check_in_time",), ("check_out_time",), ("check_in_time", "check_out_time")])
async def test_unknown_hours_on_validated_u2_offer_keep_other_s5_rules_active(missing):
    party = Travelers(adults=2, children_ages=[5], pets=1)
    original = await offered_stay("MLE", JEN, party)
    stay = HotelOffer.model_validate(original.model_dump() | dict.fromkeys(missing))
    flight = await outbound_for(stay, party)
    quote = (await transfer_quotes(stay, flight, party))[0]
    leg = transfer_leg(quote)
    inbound = min(
        await FlightTool().run(
            FlightSearch(origin="MLE", destination="SVO", departure_date=stay.check_out, travelers=party)
        ),
        key=lambda item: item.departure_at,
    )
    timeline = build_timeline(flight, stay, inbound, arrival_transfer=leg)
    issues = validate_trip(
        timeline,
        stay,
        party,
        arrival_transfer=leg,
        requirements=Requirements(refundable_required=True),
        airport="MLE",
        base=TransferBase.load(),
    )
    by_rule = {issue.rule: issue for issue in issues}
    for field, event, wait in (
        ("check_in_time", "check_in", "hotel_before_check_in"),
        ("check_out_time", "check_out", "after_check_out"),
    ):
        if field in missing:
            assert getattr(timeline, "booked_" + event) is None
            assert timeline.event(event) is None
            assert timeline.wait(wait) is None
        else:
            assert getattr(timeline, "booked_" + event) is not None
            assert timeline.event(event) is not None
    assert timeline.event("return_departure").at == inbound.departure_at
    assert timeline.hotel_arrival_at == leg.arrive_at
    unknown = by_rule["hotel_times_unknown"]
    assert unknown.severity == "warning"
    assert unknown.fixes[0].price_delta_rub is None
    assert set(unknown.fixes[0].patch["fields"]) == set(missing)
    assert by_rule["nonrefundable_rate"].severity == "critical"
    assert by_rule["transfer_pets"].severity == "warning"
    assert by_rule["transfer_child_seat"].severity == "warning"
    assert original.check_in_time is not None and original.check_out_time is not None


async def test_real_ayt_minivan_preserves_mode_and_prebooking_in_s5():
    party = Travelers(adults=5)
    stay = await offered_stay("AYT", "osm:node/3623376904", party)
    flight = await outbound_for(stay, party)
    quote = next(item for item in await transfer_quotes(stay, flight, party) if item.mode == "minivan")
    leg = transfer_leg(quote)
    issues = validate_trip(
        build_timeline(flight, stay, arrival_transfer=leg),
        stay,
        party,
        arrival_transfer=leg,
        airport="AYT",
        base=TransferBase.load(),
    )
    prebook = next(issue for issue in issues if issue.rule == "transfer_prebook")
    assert leg.mode == "private_minivan"
    assert quote.vehicles == 1 and quote.price_rub == leg.price_rub == 3350
    assert quote.prebook_hours == 24 and "prebook_required" in leg.issues
    assert prebook.severity == "info" and "24" in prebook.message


async def test_d3_far_transfer_scenarios_do_not_replace_s4_route_duration():
    party = Travelers(adults=2)
    traps = [
        hotel for hotel in load_hotel_catalog() if get_synthetic_rules(hotel.hotel_id)["scenario"] == "far_transfer"
    ]
    assert len(traps) == 12 and {hotel.destination for hotel in traps} == {"AYT", "DXB", "MLE"}
    for record in traps:
        stay = await offered_stay(record.destination, record.hotel_id, party)
        flight = await outbound_for(stay, party)
        rules = get_synthetic_rules(record.hotel_id)
        quote = (await transfer_quotes(stay, flight, party))[0]
        expected = next(
            option
            for option in TransferBase.load().get(record.hotel_id, record.destination).options
            if option.id == quote.id
        )
        leg = transfer_leg(quote)
        issues = validate_trip(build_timeline(flight, stay, arrival_transfer=leg), stay, party, arrival_transfer=leg)
        assert rules["synthetic_airport_transfer_minutes"] == 120
        assert quote.duration_minutes == expected.duration_min
        assert leg.arrive_at.timestamp() - leg.depart_at.timestamp() == expected.duration_min * 60
        assert any(issue.rule == "long_transfer" for issue in issues) == (expected.duration_min > 90)


async def test_real_dubai_offer_has_priced_s4_route_and_s3_events():
    party = Travelers(adults=2, children_ages=[5])
    offers = await HotelSearchTool().run(
        HotelSearch(destination="DXB", check_in=date(2026, 11, 18), check_out=date(2026, 11, 25), travelers=party)
    )
    assert offers
    for stay in offers:
        flight = await outbound_for(stay, party)
        quotes = await transfer_quotes(stay, flight, party)
        assert quotes and all(quote.destination.id == stay.hotel_id for quote in quotes)
        leg = transfer_leg(quotes[0])
        timeline = build_timeline(flight, stay, arrival_transfer=leg)
        assert timeline.event("arrival_transfer_departure").at == leg.depart_at
        assert timeline.event("arrival_transfer_arrival").at == leg.arrive_at
        assert not any(note.startswith("Нет трансфера из аэропорта:") for note in timeline.notes)


@pytest.mark.parametrize("airport,hotel_id", [("DXB", "osm:node/999999999999999"), ("DXB", JEN)])
async def test_unknown_hotel_and_wrong_airport_remain_explicit_errors(airport, hotel_id):
    request = TransferRequest(
        airport=airport,
        hotel_id=hotel_id,
        pickup_at=datetime(2026, 11, 18, 23, 40, tzinfo=UTC),
        travelers=Travelers(adults=2),
    )
    with pytest.raises(UnsupportedTransfer, match=f"Нет трансфера {airport}"):
        await S4TransferTool().run(request)
    with pytest.raises(UnsupportedTransfer):
        TransferBase.load().get(hotel_id, airport)


async def test_maldives_2340_arrival_waits_for_six_am_seaplane_and_reaches_s5():
    party = Travelers(adults=2)
    stay = await offered_stay("MLE", "osm:node/4455235191", party)
    generated = await outbound_for(stay, party)
    # Deliberate schedule trap validated as a D1 flight, using the actual D3
    # Conrad offer and unmodified S4 seaplane hours/prices.
    arrival = datetime.combine(stay.check_in, time(23, 40), tzinfo=generated.arrival_at.tzinfo)
    flight = type(generated).model_validate(
        generated.model_dump()
        | {
            "arrival_at": arrival,
            "departure_at": (arrival - timedelta(hours=9)).astimezone(generated.departure_at.tzinfo),
        }
    )
    quotes = await transfer_quotes(stay, flight, party)
    assert len(quotes) == 1
    quote = quotes[0]
    leg = transfer_leg(quote)
    timeline = build_timeline(flight, stay, arrival_transfer=leg)
    issues = validate_trip(timeline, stay, party, arrival_transfer=leg, airport="MLE", base=TransferBase.load())
    by_rule = {issue.rule: issue for issue in issues}
    assert quote.mode == "seaplane" and quote.available is False
    assert quote.hours.from_ == time(6) and quote.hours.to == time(15, 30)
    assert quote.pickup_at.date() == stay.check_in + timedelta(days=1)
    assert quote.pickup_at.time() == time(0, 25)
    assert quote.depart_at == datetime.combine(stay.check_in + timedelta(days=1), time(6), tzinfo=arrival.tzinfo)
    assert quote.price_rub == leg.price_rub == 66_000
    assert timeline.wait("airport_after_arrival").minutes == 335
    assert timeline.hotel_arrival_at == leg.arrive_at
    assert by_rule["airport_overnight_wait"].severity == "critical"
    assert by_rule["transfer_prebook"].severity == "info"
    assert by_rule["wasted_first_night"].severity == "info"


async def test_factory_composes_u2_search_s4_and_s5_with_one_party_and_saved_base(tmp_path):
    settings = Settings(_env_file=None, cache_mode="replay", data_cache_dir=tmp_path, data_seed=777)
    party = Travelers(adults=2, children_ages=[5], pets=1)
    async with open_data_services(settings) as services:
        stays = await services.hotels.run(
            HotelSearch(destination="MLE", check_in=date(2026, 11, 18), check_out=date(2026, 11, 25), travelers=party)
        )
        stay = next(offer for offer in stays if offer.hotel_id == JEN)
        flights = await services.flights.run(
            FlightSearch(origin="SVO", destination="MLE", departure_date=stay.check_in, travelers=party)
        )
        flight = next(offer for offer in flights if offer.departure_at.hour == 9)
        quote = (
            await services.transfers.run(
                TransferRequest(
                    airport="MLE",
                    hotel_id=stay.hotel_id,
                    pickup_at=flight.arrival_at + timedelta(minutes=AIRPORT_EXIT_MIN),
                    travelers=party,
                )
            )
        )[0]
        leg = transfer_leg(quote)
        issues = validate_trip(
            build_timeline(flight, stay, arrival_transfer=leg),
            stay,
            party,
            arrival_transfer=leg,
            requirements=Requirements(refundable_required=True),
            airport="MLE",
            base=services.transfer_base,
        )
        assert services.transfers.base is services.transfer_base
        assert flight.id.endswith("s777")
        assert quote.destination.id == stay.hotel_id
        assert quote.vehicles == 1 and quote.price_rub == 1100
        by_rule = {issue.rule: issue for issue in issues}
        assert by_rule["nonrefundable_rate"].severity == "critical"
        assert by_rule["transfer_pets"].severity == "warning"
        assert by_rule["transfer_child_seat"].severity == "warning"
    assert list(tmp_path.iterdir()) == []
