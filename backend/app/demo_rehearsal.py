"""Autonomous O2/D9 data rehearsal; no chat orchestration, bookings, paid LLM or network."""

import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

from app.booking.timeline import AIRPORT_EXIT_MIN, build_timeline
from app.booking.validator import validate_trip
from app.config import Settings
from app.contracts.identifiers import canonical_hotel_id
from app.contracts.tools import FlightSearch, HotelSearch, PriceCalendarQuery, TrainSearch, Travelers
from app.data_paths import resolve_data_dir
from app.tools.currency import load_cbr_rates
from app.tools.destinations import suggest_destinations
from app.tools.flights import FlightTool, generate_flights
from app.tools.hotels import search_hotel_results
from app.tools.http_cache import CachedHttpClient
from app.tools.price_calendar import TripQuote, price_calendar, suggest_date_shifts
from app.tools.registry import create_tools
from app.tools.s4_adapter import transfer_leg
from app.tools.trains import generate_trains
from app.tools.transfers import TransferBase


async def quote_demo_trip(
    day: date,
    *,
    hotel_id: str,
    destination: str,
    nights: int,
    travelers: Travelers,
    seed: int = 42,
) -> TripQuote | None:
    """Complete comparable demo quote, same-day arrival and fixed physical hotel.

    Overnight arrivals are excluded from this simplified pricing example rather
    than silently omitting an extra hotel night. S3/S5 own the final timeline.
    """
    hotel_id = canonical_hotel_id(hotel_id)
    try:
        end = day + timedelta(days=nights)
    except OverflowError:
        return None
    out = [
        flight
        for flight in generate_flights(
            FlightSearch(origin="SVO", destination=destination, departure_date=day, travelers=travelers), seed=seed
        )
        if flight.arrival_at.date() == day
    ]
    back = generate_flights(
        FlightSearch(origin=destination, destination="SVO", departure_date=end, travelers=travelers), seed=seed
    )
    hotels = await search_hotel_results(
        HotelSearch(destination=destination, check_in=day, check_out=end, travelers=travelers, refundable_only=True)
    )
    selected = next((item for item in hotels if item.hotel_id == hotel_id), None)
    if not out or not back or selected is None:
        return None
    outbound = min(out, key=lambda item: item.price_rub + item.baggage_price_rub)
    inbound = min(back, key=lambda item: item.price_rub + item.baggage_price_rub)
    return TripQuote(
        departure_date=day,
        nights=nights,
        origin="SVO",
        destination=destination,
        hotel_id=hotel_id,
        travelers=travelers,
        flight_total_rub=sum(item.price_rub + item.baggage_price_rub for item in (outbound, inbound)),
        hotel_total_rub=selected.offer.price_rub,
        is_synthetic=True,
    )


async def demo_payload(*, seed: int = 42, destination: str = "DXB", departure: date = date(2026, 11, 17)) -> dict:
    try:
        end = departure + timedelta(days=7)
    except OverflowError as exc:
        raise ValueError("Демо требует семи календарных дней после даты вылета") from exc
    party = Travelers(adults=2, children_ages=[5])
    flights = await FlightTool(mode="synthetic", seed=seed).search_with_provenance(
        FlightSearch(origin="SVO", destination=destination, departure_date=departure, travelers=party)
    )
    hotels = await search_hotel_results(
        HotelSearch(destination=destination, check_in=departure, check_out=end, travelers=party, refundable_only=True)
    )
    quote = None
    for hotel in hotels:
        quote = await quote_demo_trip(
            departure, hotel_id=hotel.hotel_id, destination=destination, nights=7, travelers=party, seed=seed
        )
        if quote is not None:
            break
    facts = ()
    if quote is not None:

        async def quote_day(day):
            return await quote_demo_trip(
                day, hotel_id=quote.hotel_id, destination=destination, nights=7, travelers=party, seed=seed
            )

        facts = await suggest_date_shifts(quote, quote_day)
    calendar_query = PriceCalendarQuery(
        origin="SVO", destination=destination, month=departure.strftime("%Y-%m"), travelers=party
    )
    calendar_result = await price_calendar(calendar_query, FlightTool(mode="synthetic", seed=seed).run)
    data_dir = resolve_data_dir()
    async with CachedHttpClient(mode="replay", cache_dir=data_dir / "cache/cbr") as http:
        rates = await load_cbr_rates(http)
    registry = create_tools(Settings(_env_file=None, tools_mode="real", llm_provider="fake", data_dir=data_dir))
    if set(registry) != {
        "search_flights",
        "search_hotels",
        "search_trains",
        "get_transfer",
        "suggest_destinations",
        "price_calendar",
    }:
        raise ValueError("Rehearsal requires the complete six-tool registry")
    trap_day = date(2026, 11, 18)
    maldives = await FlightTool().run(FlightSearch(origin="SVO", destination="MLE", departure_date=trap_day))
    evening_arrival = next(item for item in maldives if item.departure_at.hour == 9)
    stays = await search_hotel_results(
        HotelSearch(destination="MLE", check_in=trap_day, check_out=trap_day + timedelta(days=7))
    )
    conrad = next(item for item in stays if item.hotel_id == "osm:node/4455235191")
    transfer_tool = registry["get_transfer"]
    from app.contracts.tools import TransferRequest

    transfer = (
        await transfer_tool.run(
            TransferRequest(
                airport="MLE",
                hotel_id=conrad.hotel_id,
                pickup_at=evening_arrival.arrival_at + timedelta(minutes=AIRPORT_EXIT_MIN),
            )
        )
    )[0]
    leg = transfer_leg(transfer)
    timeline = build_timeline(evening_arrival, conrad.offer, arrival_transfer=leg)
    issues = validate_trip(
        timeline, conrad.offer, Travelers(), arrival_transfer=leg, airport="MLE", base=TransferBase.load()
    )
    if not any(issue.rule == "airport_overnight_wait" and issue.severity == "critical" for issue in issues):
        raise ValueError("Expected the Maldives night-transfer trap")
    if not hotels or not all(item.offer.review_profile is not None for item in hotels):
        raise ValueError("D7 profiles are absent from hotel search")
    return {
        "demo": True,
        "registry_tools": sorted(registry),
        "maldives_trap": {
            "flight": evening_arrival.model_dump(mode="json"),
            "transfer": transfer.model_dump(mode="json"),
            "issues": [issue.model_dump(mode="json") for issue in issues],
        },
        "scope": "data_only_no_chat_or_booking",
        "savings_price_scope": "round_trip_flights_with_baggage_and_hotel_only; transfers_and_other_extras_not_priced",
        "seed": seed,
        "notice": "Расписания, цены, правила, доступность и отзывы — синтетика; названия и координаты отелей — OSM.",
        "destinations": suggest_destinations(
            departure.month, {"tags": ["beach"], "min_water_temp_c": 24}, departure_date=departure, limit=3
        ).model_dump(mode="json"),
        "flights": [item.model_dump(mode="json") for item in flights.flights],
        "flight_provenance": flights.provenance,
        "matching_hotels_count": len(hotels),
        "hotels": [item.model_dump(mode="json") for item in hotels[:3]],
        "trains": [
            item.model_dump(mode="json")
            for item in generate_trains(
                TrainSearch(origin="LED", destination="MOW", departure_date=departure, travelers=party), seed=seed
            )
        ],
        "calendar": calendar_result.model_dump(mode="json"),
        "selected_quote": quote.model_dump(mode="json") if quote else None,
        "date_savings": [item.model_dump(mode="json") for item in facts],
        "currency": {
            "source": rates.source_url,
            "effective_date": rates.effective_date.isoformat(),
            "usd_100_rub": rates.convert(100, "USD").amount_rub,
            "is_historical_snapshot": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--destination", choices=("AYT", "DXB", "MLE"), default="DXB")
    parser.add_argument("--departure-date", type=date.fromisoformat, default=date(2026, 11, 17))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = asyncio.run(demo_payload(seed=args.seed, destination=args.destination, departure=args.departure_date))
    except ValueError as exc:
        parser.error(str(exc))
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    if args.json:
        print(serialized)
    else:
        print(payload["notice"])
        print("Срез данных У2, без чата, бронирования и оплаты. Сеть не используется.")
        print(f"Рейсов: {len(payload['flights'])}; совместимых отелей: {payload['matching_hotels_count']}.")
        print(f"Поездов: {len(payload['trains'])}; дней календаря: {len(payload['calendar']['days'])}.")
        print(f"Курсы ЦБ: {payload['currency']['effective_date']} (сохранённый снимок).")
        for item in payload["date_savings"]:
            print(f"Сдвиг {item['shift_days']:+} дн.: экономия {item['saving_rub']} ₽, перелёт туда-обратно + отель.")
        print("Подробные источники, правила и профили: --json или --output /tmp/demo-data.json.")


if __name__ == "__main__":
    main()
