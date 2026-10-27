"""Offline D6 demonstration; no provider keys, timetable API or booking."""

import argparse
import asyncio
import json
from datetime import date

from app.contracts.tools import TrainSearch, Travelers
from app.tools.trains import TrainTool


async def demo_payload(origin="LED", destination="MOW", departure=date(2026, 11, 18)):
    tool = TrainTool()
    results = {}
    for seat in ("seated", "platzkart", "coupe", "sv"):
        response = await tool.search_with_provenance(
            TrainSearch(
                origin=origin,
                destination=destination,
                departure_date=departure,
                seat_class=seat,
                travelers=Travelers(adults=2),
            )
        )
        results[seat] = {
            "trains": [item.model_dump(mode="json") for item in response.trains],
            "provenance": list(response.provenance),
        }
    return {"scope": "synthetic_data_only_no_booking", "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", choices=("LED", "MOW", "SVO"), default="LED")
    parser.add_argument("--destination", choices=("LED", "MOW", "SVO"), default="MOW")
    parser.add_argument("--departure-date", type=date.fromisoformat, default=date(2026, 11, 18))
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(demo_payload(args.origin, args.destination, args.departure_date)), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
