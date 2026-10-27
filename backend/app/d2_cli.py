"""Standalone D2 demo: seeded flights, with no HTTP client or credentials."""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import date
from typing import Any

from pydantic import ValidationError

from app.contracts.tools import FlightSearch, Travelers
from app.tools.flights import FlightTool


async def demo_payload(
    *,
    origin: str = "SVO",
    destination: str = "DXB",
    departure_date: date = date(2026, 11, 18),
    seed: int = 42,
) -> dict[str, Any]:
    """Search for two adults and a five-year-old in synthetic mode only."""
    query = FlightSearch(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        travelers=Travelers(adults=2, children_ages=[5]),
    )
    result = await FlightTool(mode="synthetic", seed=seed).search_with_provenance(query)
    return {
        "scope": "d2_flights_only",
        "mode": "synthetic",
        "seed": seed,
        "one_way": True,
        "price_basis": "RUB for the entire group; baggage is a separate additional group price",
        "query": query.model_dump(mode="json"),
        "flights": [flight.model_dump(mode="json") for flight in result.flights],
        "provenance": list(result.provenance),
    }


def _print_demo(payload: dict[str, Any]) -> None:
    query, flights = payload["query"], payload["flights"]
    print("D2: синтетические расписание и цены, без проверки наличия билетов.")
    print(f"{query['origin']} → {query['destination']}, дата вылета {query['departure_date']}.")
    print("Группа: 2 взрослых и ребёнок 5 лет. Цена в одну сторону за всю группу; багаж оплачивается отдельно.")
    print("Время местное: дата, время, UTC-смещение и часовой пояс указаны для каждого аэропорта.")
    print(
        f"Найдено рейсов: {len(flights)}. С прилётом на следующую местную дату: "
        f"{sum(flight['is_overnight'] for flight in flights)}."
    )
    for flight in flights:
        print(
            f"{flight['origin']['iata']} {flight['departure_at']} ({flight['origin']['timezone']}) → "
            f"{flight['destination']['iata']} {flight['arrival_at']} ({flight['destination']['timezone']}): "
            f"{flight['price_rub']} RUB за группу + {flight['baggage_price_rub']} RUB багаж; "
            f"пересадок: {flight['stops']}."
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D2: автономное демо синтетического поиска авиабилетов")
    parser.add_argument("--json", action="store_true", help="вывести DTO и происхождение данных в JSON")
    parser.add_argument("--origin", default="SVO", help="IATA-код аэропорта вылета, например SVO или LED")
    parser.add_argument("--destination", default="DXB", help="IATA-код аэропорта назначения: AYT, DXB или MLE")
    parser.add_argument("--departure-date", type=date.fromisoformat, default=date(2026, 11, 18), metavar="YYYY-MM-DD")
    parser.add_argument("--seed", type=int, default=42, help="число для воспроизводимых расписания и цены")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        payload = asyncio.run(
            demo_payload(
                origin=args.origin,
                destination=args.destination,
                departure_date=args.departure_date,
                seed=args.seed,
            )
        )
    except ValidationError:
        print("Ошибка запроса: проверьте IATA-коды и дату вылета.", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_demo(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
