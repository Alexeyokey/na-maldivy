"""Поведенческие проверки воспроизводимого D1-каталога без сети и моделей."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.contracts.tools import (
    DestinationQuery,
    FlightSearch,
    HotelSearch,
    Place,
    PriceCalendarQuery,
    TrainSearch,
    TransferQuery,
    Travelers,
)
from app.tools.stubs import create_stub_tools


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
    solo = await transfer_tool.run(query)
    four = await transfer_tool.run(query.model_copy(update={"travelers": Travelers(adults=4)}))
    five = await transfer_tool.run(query.model_copy(update={"travelers": Travelers(adults=5)}))
    night = await transfer_tool.run(await transfer_query(tools, hour=23))
    assert solo.price_rub == four.price_rub
    assert five.price_rub == 2 * solo.price_rub
    assert night.price_rub > solo.price_rub
    assert night.duration_minutes == solo.duration_minutes
    assert solo.price_rub > 0 and solo.duration_minutes > 0 and solo.is_synthetic
    # pickup_at может содержать фиксированное UTC-смещение, а не объект ZoneInfo.
    offset_only = query.model_copy(update={"pickup_at": datetime.fromisoformat("2030-11-10T23:00:00+04:00")})
    assert (await transfer_tool.run(offset_only)).price_rub == night.price_rub


async def test_transfer_rejects_unknown_coordinates_cross_destination_and_same_point(tools):
    query = await transfer_query(tools)
    invalid_places = [
        Place(iata="AAA", name="Unknown", latitude=0, longitude=0, timezone="Asia/Dubai"),
        query.destination.model_copy(update={"latitude": 0}),
        query.destination.model_copy(update={"iata": "AYT", "timezone": "Europe/Istanbul"}),
        query.origin,
    ]
    for destination in invalid_places:
        with pytest.raises(ValueError, match="Unsupported demo transfer route"):
            await tools["get_transfer"].run(query.model_copy(update={"destination": destination}))


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
