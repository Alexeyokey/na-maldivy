"""Export deterministic D1 request/response fixtures for S4 and O1 reviewers."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.tools.registry import TOOLS, run_tool  # noqa: E402


async def pair(name: str, arguments: dict) -> dict:
    tool = TOOLS[name]
    request = tool.input_model.model_validate(arguments)
    result = await run_tool(name, arguments)
    return {
        "input": request.model_dump(mode="json"),
        "output": tool.output_adapter.dump_python(result, mode="json"),
    }


async def build_examples() -> dict[str, str]:
    inputs = {
        "search_flights": {"origin": "SVO", "destination": "MLE", "departure_date": "2026-11-18"},
        "search_hotels": {"destination": "MLE", "check_in": "2026-11-18", "check_out": "2026-11-25"},
        "search_trains": {"origin": "LED", "destination": "MOW", "departure_date": "2026-11-18"},
        "suggest_destinations": {"month": 11, "origin": "SVO", "min_water_temperature_c": 24},
        "price_calendar": {"origin": "SVO", "destination": "MLE", "month": "2026-11"},
    }
    examples = {name: await pair(name, args) for name, args in inputs.items()}
    hotel = examples["search_hotels"]["output"][0]
    night_query = {"airport": "MLE", "hotel_id": hotel["hotel_id"], "pickup_at": "2026-11-18T23:40:00+05:00"}
    examples["get_transfer"] = await pair("get_transfer", night_query)
    antalya_hotels = await pair(
        "search_hotels", {"destination": "AYT", "check_in": "2026-11-18", "check_out": "2026-11-25"}
    )
    group_query = {
        "airport": "AYT",
        "hotel_id": antalya_hotels["output"][0]["hotel_id"],
        "pickup_at": "2026-11-18T12:00:00+03:00",
        "travelers": {"adults": 4, "children_ages": [1]},
    }
    cases = [
        {
            "id": "male-night-seaplane",
            "description": "Requested 23:40; next daytime seaplane after 06:00.",
            **examples["get_transfer"],
        },
        {
            "id": "group-five-including-infant",
            "description": "Five humans use two taxis; infant passenger fares are free.",
            **await pair("get_transfer", group_query),
        },
    ]
    result = {}
    for filename, data in (("tools.json", examples), ("s4-transfers.json", cases)):
        result[filename] = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, content in asyncio.run(build_examples()).items():
        path = ROOT / "contracts/examples" / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                raise SystemExit(f"Examples differ: {name}; run make tool-examples")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        print(f"{'Checked' if args.check else 'Saved'} {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
