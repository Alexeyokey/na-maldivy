"""Поведенческие проверки воспроизводимого D1-каталога без сети и моделей."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.contracts.tools import (
    DestinationQuery,
    FlightSearch,
    HotelSearch,
    Place,
    PriceCalendarQuery,
    TrainSearch,
    Transfer,
    TransferHours,
    TransferQuery,
    Travelers,
)
from app.tools.errors import UnsupportedTransfer
from app.tools.stubs import _next_departure, create_stub_tools


@pytest.fixture
def tools():
    return create_stub_tools()


@pytest.mark.parametrize("destination", ["AYT", "DXB", "MLE"])
async def test_three_destinations_and_actual_overnight_arrival(tools, destination):
    flights = await tools["search_flights"].run(
        FlightSearch(
            origin="SVO",
            destination=destination,
            departure_date=date(2030, 11, 10),
            direct_only=True,
        )
    )
    assert len(flights) == 2
    assert all(f.origin.iata == "SVO" and f.destination.iata == destination and f.stops == 0 for f in flights)
    assert all(f.is_synthetic for f in flights)
    night = flights[1]
    assert night.departure_at.date() == date(2030, 11, 10)
    assert night.arrival_at.date() == date(2030, 11, 11)
    assert night.is_overnight
    assert night.arrival_at.astimezone(UTC) > night.departure_at.astimezone(UTC)
    if destination == "DXB":
        assert night.departure_at.isoformat() == "2030-11-10T23:30:00+03:00"
        assert night.arrival_at.isoformat() == "2030-11-11T06:00:00+04:00"
        assert night.arrival_at.astimezone(UTC) - night.departure_at.astimezone(UTC) == timedelta(hours=5, minutes=30)


async def test_flight_alias_reverse_led_and_unsupported_routes(tools):
    flight_tool = tools["search_flights"]
    arguments = {"destination": "MLE", "departure_date": date(2030, 11, 10)}
    svo = await flight_tool.run(FlightSearch(origin="SVO", **arguments))
    mow = await flight_tool.run(FlightSearch(origin="MOW", **arguments))
    assert svo == mow
    led = await flight_tool.run(FlightSearch(origin="LED", **arguments))
    assert led[0].origin.iata == "LED" and led[0].price_rub > svo[0].price_rub
    reverse = await flight_tool.run(
        FlightSearch(
            origin="MLE",
            destination="MOW",
            departure_date=date(2030, 11, 18),
        )
    )
    assert reverse[0].origin.iata == "MLE" and reverse[0].destination.iata == "SVO"
    assert reverse[0].departure_at.utcoffset() == timedelta(hours=5)
    assert reverse[0].arrival_at.utcoffset() == timedelta(hours=3)
    for origin, destination in [("AAA", "MLE"), ("AYT", "DXB"), ("SVO", "SVO")]:
        assert (
            await flight_tool.run(
                FlightSearch(
                    origin=origin,
                    destination=destination,
                    departure_date=date(2030, 11, 10),
                )
            )
            == []
        )


async def test_flight_price_is_whole_group_and_budget_inclusive(tools):
    query = FlightSearch(
        origin="SVO",
        destination="DXB",
        departure_date=date(2030, 11, 10),
        travelers=Travelers(adults=2, children_ages=[8], pets=1),
    )
    flights = await tools["search_flights"].run(query)
    assert flights[0].price_rub == 81_750  # 26 750 × 3 места + 1 500 за животное.
    assert flights[0].baggage_price_rub == 6_000
    exact_budget = await tools["search_flights"].run(query.model_copy(update={"max_price_rub": 81_750}))
    assert exact_budget == [flights[0]]
    assert await tools["search_flights"].run(query.model_copy(update={"max_price_rub": 81_749})) == []


@pytest.mark.parametrize("destination", ["AYT", "DXB", "MLE"])
async def test_hotel_filters_children_pets_stars_refund_and_stay(tools, destination):
    query = HotelSearch(
        destination=destination,
        check_in=date(2030, 11, 10),
        check_out=date(2030, 11, 13),
        travelers=Travelers(adults=2, children_ages=[8], pets=1),
        min_stars=4,
        refundable_only=True,
    )
    hotels = await tools["search_hotels"].run(query)
    assert len(hotels) == 2
    assert all(not h.adults_only and h.pets_allowed and h.refundable and h.stars >= 4 for h in hotels)
    assert all(h.check_in == query.check_in and h.check_out == query.check_out and h.is_synthetic for h in hotels)
    cheapest = min(h.price_rub for h in hotels)
    budget = query.model_copy(update={"max_price_rub": cheapest})
    assert len(await tools["search_hotels"].run(budget)) == 1
    assert await tools["search_hotels"].run(query.model_copy(update={"max_price_rub": cheapest - 1})) == []
    if destination == "AYT":
        assert cheapest == 30_900  # (4 800 × 2 номера + 700 за животное) × 3 ночи.


async def test_hotel_catalogue_exposes_traps_and_unsupported_destination(tools):
    query = HotelSearch(destination="AYT", check_in=date(2030, 11, 10), check_out=date(2030, 11, 11))
    hotels = await tools["search_hotels"].run(query)
    assert any(h.adults_only for h in hotels)
    assert any(not h.refundable and h.reception_closes_at is not None for h in hotels)
    assert any(h.early_check_in_price_rub for h in hotels)
    assert await tools["search_hotels"].run(query.model_copy(update={"destination": "AAA"})) == []


@pytest.mark.parametrize(
    "seat_class,price", [("seated", 3_500), ("platzkart", 4_500), ("coupe", 7_000), ("sv", 15_000)]
)
async def test_train_class_group_price_dates_and_city_alias(tools, seat_class, price):
    query = TrainSearch(
        origin="SVO",
        destination="LED",
        departure_date=date(2030, 11, 10),
        seat_class=seat_class,
        travelers=Travelers(adults=2, children_ages=[6], pets=1),
    )
    trains = await tools["search_trains"].run(query)
    assert len(trains) == 1
    train = trains[0]
    assert train.origin.iata == "MOW" and train.origin.name == "Москва"
    assert train.destination.latitude == 59.9301  # Городская точка, а не аэропорт LED.
    assert train.arrival_at.date() == date(2030, 11, 11)
    assert train.price_rub == price * 3 + 500
    assert train.seat_class == seat_class and train.is_synthetic
    reverse = await tools["search_trains"].run(query.model_copy(update={"origin": "LED", "destination": "MOW"}))
    assert reverse[0].origin.iata == "LED" and reverse[0].destination.iata == "MOW"
    assert await tools["search_trains"].run(query.model_copy(update={"destination": "AYT"})) == []


async def transfer_query(tools, hour=12, adults=1):
    flights = await tools["search_flights"].run(
        FlightSearch(
            origin="SVO",
            destination="DXB",
            departure_date=date(2030, 11, 10),
        )
    )
    hotels = await tools["search_hotels"].run(
        HotelSearch(
            destination="DXB",
            check_in=date(2030, 11, 10),
            check_out=date(2030, 11, 12),
        )
    )
    return TransferQuery(
        origin=flights[0].destination,
        destination=hotels[0].place,
        pickup_at=datetime(2030, 11, 10, hour, tzinfo=ZoneInfo("Asia/Dubai")),
        travelers=Travelers(adults=adults),
    )


async def test_transfer_group_vehicle_capacity_and_local_night_coefficient(tools):
    transfer_tool = tools["get_transfer"]
    query = await transfer_query(tools)

    def taxi(options):
        return next(item for item in options if item.mode == "taxi")

    solo = taxi(await transfer_tool.run(query))
    four = taxi(await transfer_tool.run(query.model_copy(update={"travelers": Travelers(adults=4)})))
    five = taxi(await transfer_tool.run(query.model_copy(update={"travelers": Travelers(adults=5)})))
    night = taxi(await transfer_tool.run(await transfer_query(tools, hour=23)))
    assert solo.price_rub == four.price_rub
    assert five.price_rub == 2 * solo.price_rub
    assert night.price_rub > solo.price_rub
    assert night.duration_minutes == solo.duration_minutes
    assert solo.price_rub > 0 and solo.duration_minutes > 0 and solo.is_synthetic
    assert solo.vehicles == four.vehicles == 1 and five.vehicles == 2
    assert solo.available and solo.hours is None and solo.depart_at == query.pickup_at
    # pickup_at может содержать фиксированное UTC-смещение, а не объект ZoneInfo.
    offset_only = query.model_copy(update={"pickup_at": datetime.fromisoformat("2030-11-10T23:00:00+04:00")})
    assert taxi(await transfer_tool.run(offset_only)).price_rub == night.price_rub


async def test_transfer_rejects_unknown_coordinates_cross_destination_and_same_point(tools):
    query = await transfer_query(tools)
    invalid_places = [
        Place(kind="city", iata="AAA", name="Unknown", latitude=0, longitude=0, timezone="Asia/Dubai"),
        query.destination.model_copy(update={"latitude": 0}),
        query.destination.model_copy(update={"iata": "AYT", "timezone": "Europe/Istanbul"}),
        query.origin,
    ]
    for destination in invalid_places:
        with pytest.raises(UnsupportedTransfer, match="Unsupported demo transfer route"):
            await tools["get_transfer"].run(query.model_copy(update={"destination": destination}))


async def test_place_kinds_and_hotel_identity_survive_date_and_party_changes(tools):
    query = HotelSearch(destination="DXB", check_in=date(2030, 11, 10), check_out=date(2030, 11, 12))
    first = await tools["search_hotels"].run(query)
    moved = await tools["search_hotels"].run(
        query.model_copy(
            update={
                "check_in": date(2030, 11, 11),
                "check_out": date(2030, 11, 13),
                "travelers": Travelers(adults=2),
            }
        )
    )
    assert {offer.hotel_id for offer in first} == {offer.hotel_id for offer in moved}
    for before, after in zip(first, moved, strict=True):
        assert before.place.kind == after.place.kind == "hotel"
        assert before.hotel_id == before.place.id == after.hotel_id == after.place.id
        assert before.offer_id != after.offer_id
        assert before.id == before.offer_id
        assert before.hotel_id.startswith("demo-dxb-")
    flights = await tools["search_flights"].run(
        FlightSearch(
            origin="SVO",
            destination="DXB",
            departure_date=date(2030, 11, 10),
        )
    )
    assert flights[0].origin.kind == flights[0].destination.kind == "airport"
    trains = await tools["search_trains"].run(
        TrainSearch(
            origin="LED",
            destination="MOW",
            departure_date=date(2030, 11, 10),
        )
    )
    assert trains[0].origin.kind == trains[0].destination.kind == "city"


async def test_hotel_reception_and_early_checkin_are_explicit_and_unknown_stays_unknown(tools):
    hotels = await tools["search_hotels"].run(
        HotelSearch(
            destination="DXB",
            check_in=date(2030, 11, 10),
            check_out=date(2030, 11, 12),
        )
    )
    budget = next(item for item in hotels if item.hotel_id == "demo-dxb-budget")
    family = next(item for item in hotels if item.hotel_id == "demo-dxb-family")
    assert budget.reception_24h is False and budget.reception_closes_at == time(22)
    assert budget.early_check_in_available is False and budget.early_check_in_price_rub is None
    assert family.reception_24h is True and family.reception_closes_at is None
    assert family.early_check_in_available is True and family.early_check_in_price_rub > 0
    unknown = budget.model_copy(
        update={
            "adults_only": None,
            "pets_allowed": None,
            "refundable": None,
            "stars": None,
            "rating": None,
            "reception_24h": None,
            "reception_closes_at": None,
            "early_check_in_available": None,
        }
    )
    encoded = tools["search_hotels"].output_adapter.dump_json([unknown])
    restored = tools["search_hotels"].output_adapter.validate_json(encoded)[0]
    assert restored.stars is None and restored.adults_only is None and restored.pets_allowed is None
    assert restored.reception_24h is None and restored.early_check_in_available is None


def by_mode(alternatives: list[Transfer], mode: str) -> Transfer:
    return next(item for item in alternatives if item.mode == mode)


async def test_transfer_list_available_first_then_full_group_price_and_public_unknown_flags(tools):
    query = await transfer_query(tools, hour=23)
    options = await tools["get_transfer"].run(query)
    assert len(options) == 5
    assert options == sorted(options, key=lambda item: (not item.available, item.price_rub, item.id))
    assert {item.mode for item in options} == {"taxi", "minivan", "bus", "metro", "shuttle"}
    assert all(item.pickup_at == query.pickup_at for item in options)
    assert all(item.depart_at.astimezone(UTC) >= query.pickup_at.astimezone(UTC) for item in options)
    assert by_mode(options, "shuttle").available is False
    assert by_mode(options, "shuttle").depart_at.date() == date(2030, 11, 11)
    assert by_mode(options, "shuttle").pets is None
    assert by_mode(options, "bus").pets is False
    assert by_mode(options, "taxi").child_seat is None
    assert by_mode(options, "minivan").child_seat is True
    assert by_mode(options, "metro").available is True  # Overnight 05:00–01:00 window.
    encoded = tools["get_transfer"].output_adapter.dump_python(options, mode="json")
    assert isinstance(encoded, list)
    assert set(next(item for item in encoded if item["mode"] == "metro")["hours"]) == {"from", "to"}


async def test_infants_count_towards_vehicle_capacity_but_public_fares_are_free_under_two(tools):
    query = await transfer_query(tools, adults=4)
    four = await tools["get_transfer"].run(query)
    infant = await tools["get_transfer"].run(
        query.model_copy(
            update={
                "travelers": Travelers(adults=4, children_ages=[1]),
            }
        )
    )
    older = await tools["get_transfer"].run(
        query.model_copy(
            update={
                "travelers": Travelers(adults=4, children_ages=[2]),
            }
        )
    )
    assert by_mode(four, "taxi").vehicles == 1
    assert by_mode(infant, "taxi").vehicles == by_mode(older, "taxi").vehicles == 2
    assert by_mode(infant, "taxi").price_rub == 2 * by_mode(four, "taxi").price_rub
    assert by_mode(infant, "minivan").vehicles == 1
    assert by_mode(infant, "shuttle").price_rub == by_mode(four, "shuttle").price_rub
    assert by_mode(older, "shuttle").price_rub == by_mode(four, "shuttle").price_rub + 600
    assert by_mode(infant, "bus").vehicles is None


async def test_male_late_seaplane_waits_to_next_local_day_and_boat_is_available(tools):
    flight = (
        await tools["search_flights"].run(
            FlightSearch(
                origin="SVO",
                destination="MLE",
                departure_date=date(2030, 11, 10),
            )
        )
    )[0]
    hotel = (
        await tools["search_hotels"].run(
            HotelSearch(
                destination="MLE",
                check_in=date(2030, 11, 10),
                check_out=date(2030, 11, 12),
            )
        )
    )[0]
    query = TransferQuery(
        origin=flight.destination,
        destination=hotel.place,
        pickup_at=datetime(2030, 12, 31, 23, 40, tzinfo=ZoneInfo("Indian/Maldives")),
        travelers=Travelers(adults=2, children_ages=[1, 2]),
    )
    options = await tools["get_transfer"].run(query)
    assert [item.mode for item in options] == ["boat", "seaplane"]
    plane = by_mode(options, "seaplane")
    assert plane.available is False and plane.depart_at.isoformat() == "2031-01-01T06:00:00+05:00"
    assert plane.hours.from_ == time(6) and plane.hours.to == time(18)
    assert plane.price_rub == 15000 * 3 and plane.vehicles is None
    assert by_mode(options, "boat").available and by_mode(options, "boat").depart_at == query.pickup_at
    daytime = query.model_copy(
        update={
            "pickup_at": datetime(2030, 11, 10, 12, tzinfo=ZoneInfo("Indian/Maldives")),
            "travelers": Travelers(adults=1, pets=1),
        }
    )
    plane_day = by_mode(await tools["get_transfer"].run(daytime), "seaplane")
    assert plane_day.available is True and plane_day.pets is False  # Timing is separate from pet eligibility.


@pytest.mark.parametrize(
    "hour,minute,available,day,depart_hour",
    [
        (6, 0, True, 10, 6),
        (17, 59, True, 10, 17),
        (18, 0, False, 11, 6),
        (5, 59, False, 10, 6),
    ],
)
def test_half_open_seaplane_hours(hour, minute, available, day, depart_hour):
    pickup = datetime(2030, 11, 10, hour, minute, tzinfo=ZoneInfo("Indian/Maldives"))
    current, departure = _next_departure(pickup, "Indian/Maldives", TransferHours(from_=time(6), to=time(18)))
    assert current is available and departure.day == day and departure.hour == depart_hour


@pytest.mark.parametrize(
    "hour,available,depart_day,depart_hour",
    [
        (22, True, 10, 22),
        (5, True, 10, 5),
        (6, False, 10, 22),
        (21, False, 10, 22),
    ],
)
def test_overnight_service_window(hour, available, depart_day, depart_hour):
    pickup = datetime(2030, 11, 10, hour, tzinfo=ZoneInfo("Asia/Dubai"))
    current, departure = _next_departure(pickup, "Asia/Dubai", TransferHours(from_=time(22), to=time(6)))
    assert current is available and departure.day == depart_day and departure.hour == depart_hour


def test_service_opening_handles_dst_gap_and_second_fold_without_negative_waits():
    zone = ZoneInfo("Europe/Berlin")
    gap_pickup = datetime(2030, 3, 31, 1, tzinfo=zone)
    current, after_gap = _next_departure(gap_pickup, "Europe/Berlin", TransferHours(from_=time(2), to=time(4)))
    assert current is False and after_gap.isoformat() == "2030-03-31T03:00:00+02:00"
    fold_pickup = datetime(2030, 10, 27, 2, 50, tzinfo=zone, fold=0)
    current, after_fold = _next_departure(
        fold_pickup,
        "Europe/Berlin",
        TransferHours(from_=time(2, 30), to=time(2, 45)),
    )
    assert current is False and after_fold.fold == 1
    assert after_fold.astimezone(UTC) - fold_pickup.astimezone(UTC) == timedelta(minutes=40)


def test_unrepresentable_next_opening_is_explicit_and_known_24h_still_immediate():
    pickup = datetime(9999, 12, 31, 23, 40, tzinfo=ZoneInfo("Indian/Maldives"))
    assert _next_departure(pickup, "Indian/Maldives", TransferHours(from_=time(6), to=time(18))) == (False, None)
    assert _next_departure(pickup, "Indian/Maldives", None) == (True, pickup)


async def test_destinations_month_water_budget_visa_and_group_cost(tools):
    tool = tools["suggest_destinations"]
    query = DestinationQuery(month=11, min_water_temperature_c=24, visa_free_only=True)
    destinations = await tool.run(query)
    assert {d.place.iata for d in destinations} == {"DXB", "MLE"}
    assert all(d.month == 11 and d.water_temperature_c >= 24 and not d.visa_required for d in destinations)
    assert all(d.is_synthetic and d.risks for d in destinations)
    cheapest = destinations[0].estimated_total_price_rub
    budget = await tool.run(query.model_copy(update={"budget_rub": cheapest}))
    assert budget == [destinations[0]]
    assert await tool.run(query.model_copy(update={"budget_rub": cheapest - 1})) == []
    group = await tool.run(query.model_copy(update={"travelers": Travelers(adults=3)}))
    assert all(
        g.estimated_total_price_rub > s.estimated_total_price_rub for g, s in zip(group, destinations, strict=True)
    )
    assert await tool.run(query.model_copy(update={"origin": "AAA"})) == []
    winter = await tool.run(DestinationQuery(month=1))
    summer = await tool.run(DestinationQuery(month=8))
    winter_ayt = next(d for d in winter if d.place.iata == "AYT")
    summer_ayt = next(d for d in summer if d.place.iata == "AYT")
    assert summer_ayt.water_temperature_c > winter_ayt.water_temperature_c


@pytest.mark.parametrize("month,length,last", [("2032-02", 29, 29), ("2030-02", 28, 28), ("2030-11", 30, 30)])
async def test_calendar_covers_month_and_matches_actual_group_flight_price(tools, month, length, last):
    query = PriceCalendarQuery(
        origin="LED",
        destination="MLE",
        month=month,
        travelers=Travelers(adults=2, children_ages=[7], pets=1),
    )
    calendar = await tools["price_calendar"].run(query)
    assert len(calendar) == length
    assert calendar[0].date.isoformat() == month + "-01"
    assert calendar[-1].date.day == last
    assert all(item.is_synthetic for item in calendar)
    assert len({item.date for item in calendar}) == length
    for item in (calendar[0], calendar[-1]):
        flights = await tools["search_flights"].run(
            FlightSearch(
                origin=query.origin,
                destination=query.destination,
                departure_date=item.date,
                travelers=query.travelers,
            )
        )
        assert item.price_rub == min(f.price_rub for f in flights)
    assert await tools["price_calendar"].run(query.model_copy(update={"destination": "AAA"})) == []


async def test_determinism_isolation_and_date_derived_ids(tools):
    tool = tools["search_flights"]
    query = FlightSearch(origin="SVO", destination="DXB", departure_date=date(2030, 11, 10))
    first = await tool.run(query)
    expected = [item.model_dump(mode="json") for item in first]
    first[0].price_rub = 1
    first[0].destination.name = "Mutated"
    second = await tool.run(query)
    assert [item.model_dump(mode="json") for item in second] == expected
    fresh_tool = create_stub_tools()["search_flights"]
    assert fresh_tool is not tool
    assert await fresh_tool.run(query) == second
    next_day = await tool.run(query.model_copy(update={"departure_date": date(2030, 11, 11)}))
    assert next_day[0].id != second[0].id
    destinations = await tools["suggest_destinations"].run(DestinationQuery(month=11))
    destinations[0].tags.append("mutated")
    destinations[0].place.name = "Mutated"
    fresh = await tools["suggest_destinations"].run(DestinationQuery(month=11))
    assert all("mutated" not in item.tags and item.place.name != "Mutated" for item in fresh)


async def test_calendar_and_journeys_handle_last_representable_date(tools):
    flights = await tools["search_flights"].run(FlightSearch(origin="SVO", destination="DXB", departure_date=date.max))
    assert len(flights) == 1  # Утренний рейс помещается в date.max, ночной — нет.
    calendar = await tools["price_calendar"].run(PriceCalendarQuery(origin="SVO", destination="DXB", month="9999-12"))
    assert len(calendar) == 31 and calendar[-1].date == date.max
    assert calendar[-1].price_rub == flights[0].price_rub
    assert await tools["search_trains"].run(TrainSearch(origin="MOW", destination="LED", departure_date=date.max)) == []
