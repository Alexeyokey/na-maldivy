"""D8 calendar demo backed by the deterministic D2 flight implementation."""

import argparse
import asyncio

from app.contracts.tools import PriceCalendarQuery, Travelers
from app.tools.flights import FlightTool
from app.tools.price_calendar import price_calendar


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="SVO")
    parser.add_argument("--destination", default="DXB")
    parser.add_argument("--month", default="2026-11")
    args = parser.parse_args()
    query = PriceCalendarQuery(
        origin=args.origin, destination=args.destination, month=args.month, travelers=Travelers(adults=2)
    )
    print(asyncio.run(price_calendar(query, FlightTool().run)).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
