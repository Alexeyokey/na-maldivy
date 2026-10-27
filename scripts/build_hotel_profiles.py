#!/usr/bin/env python3
"""Build/check synthetic reviews and profiles; hotel facts and rules are read-only."""

# ruff: noqa: E402
# Imports below the path bootstrap let this standalone script find the backend package.
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.contracts.hotel_profiles import HotelReviewProfile
from app.contracts.identifiers import canonical_hotel_id
from app.tools.hotel_profiles import (
    GENERATOR,
    SyntheticReview,
    build_profile,
    extract_lexical,
    extract_reviews_llm,
    make_synthetic_reviews,
)


def filename(hotel_id: str) -> str:
    return canonical_hotel_id(hotel_id).replace(":", "-").replace("/", "-") + ".json"


def encoded(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


async def run(args: argparse.Namespace) -> dict:
    if args.check and (args.generate_reviews or args.mode != "lexical"):
        raise ValueError("--check is offline/read-only; use lexical mode without --generate-reviews")
    if args.mode == "llm" and (not args.allow_live_llm or args.generate_reviews):
        raise ValueError("LLM mode needs --allow-live-llm and a saved corpus; no provider was contacted")
    root = args.data_dir.resolve()
    hotels = json.loads((root / "hotels/catalog.json").read_text(encoding="utf-8"))["hotels"]
    rules = json.loads((root / "hotels/synthetic_rules.json").read_text(encoding="utf-8"))["hotels"]
    ids = {row["hotel_id"] for row in hotels}
    by_id = {row["hotel_id"]: row for row in rules}
    if len(ids) != len(hotels) or len(by_id) != len(rules) or set(by_id) != ids:
        raise ValueError("Hotel catalogue and synthetic rules must contain identical unique IDs")
    selected = canonical_hotel_id(args.hotel_id) if args.hotel_id else None
    if selected is not None and selected not in ids:
        raise ValueError("Selected hotel is absent from the catalogue")
    expected_names = {filename(hotel_id) for hotel_id in ids}
    reviews_dir = root / "hotel_reviews"
    profiles_dir = args.output_dir or root / ("hotel_profiles_llm" if args.mode == "llm" else "hotel_profiles")
    if args.generate_reviews:
        reviews_dir.mkdir(parents=True, exist_ok=True)
        for hotel_id in sorted(ids):
            if selected is not None and hotel_id != selected:
                continue
            reviews = make_synthetic_reviews(hotel_id, by_id[hotel_id])
            document = {
                "schema_version": 1,
                "hotel_id": hotel_id,
                "is_synthetic": True,
                "generator": GENERATOR,
                "real_guest_reviews_used": False,
                "reviews": [review.model_dump(mode="json") for review in reviews],
            }
            (reviews_dir / filename(hotel_id)).write_text(encoded(document), encoding="utf-8")
    if args.check:
        for directory in (reviews_dir, profiles_dir):
            if {file.name for file in directory.glob("*.json")} != expected_names:
                raise ValueError("Corpus/profile files must cover exactly the hotel catalogue")
    corpora = []
    for hotel_id in sorted(ids):
        if selected is not None and hotel_id != selected:
            continue
        document = json.loads((reviews_dir / filename(hotel_id)).read_text(encoding="utf-8"))
        if document.get("hotel_id") != hotel_id or document.get("is_synthetic") is not True:
            raise ValueError("Corpus metadata changed hotel identity or synthetic provenance")
        if document.get("real_guest_reviews_used") is not False:
            raise ValueError("Expected a wholly synthetic review corpus")
        reviews = [SyntheticReview.model_validate(row) for row in document["reviews"]]
        if not 20 <= len(reviews) <= 50 or any(row.hotel_id != hotel_id for row in reviews):
            raise ValueError("Expected 20..50 synthetic reviews for the selected hotel")
        if args.check and reviews != make_synthetic_reviews(hotel_id, by_id[hotel_id]):
            raise ValueError("Saved corpus differs from its deterministic generator")
        corpora.append((hotel_id, reviews))
    client = None
    if args.mode == "llm":
        from app.config import Settings
        from app.llm import create_llm_client

        settings = Settings()
        if settings.llm_provider != "openai":
            raise ValueError("Live extraction needs a configured non-fake provider; no provider was contacted")
        client = create_llm_client(settings)
    count = 0
    try:
        for hotel_id, reviews in corpora:
            if client is not None:
                extracted, model = await extract_reviews_llm(client, reviews)
                profile = build_profile(
                    reviews, extracted, method="llm-literal-evidence-v1", model=model, live_model_run=True
                )
            else:
                extracted = extract_lexical(reviews)
                if any(item.mentions != review.annotations for item, review in zip(extracted, reviews, strict=True)):
                    raise ValueError("Lexical extraction disagrees with fixture annotations")
                profile = build_profile(reviews, extracted)
            HotelReviewProfile.model_validate(profile)
            output = profiles_dir / filename(hotel_id)
            if args.check:
                if json.loads(output.read_text(encoding="utf-8")) != profile:
                    raise ValueError("Saved profile differs from reproducible output")
            else:
                profiles_dir.mkdir(parents=True, exist_ok=True)
                output.write_text(encoded(profile), encoding="utf-8")
            count += 1
    finally:
        if client is not None:
            await client.aclose()
    return {
        "profiles": count,
        "mode": args.mode,
        "live_model_calls": count if client is not None else 0,
        "corpus_kind": "synthetic_templates",
        "read_only_check": args.check,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--generate-reviews", action="store_true")
    parser.add_argument("--mode", choices=("lexical", "llm"), default="lexical")
    parser.add_argument("--allow-live-llm", action="store_true")
    parser.add_argument("--hotel-id")
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except (ValueError, OSError):
        print(
            "Profile build failed: check corpus, arguments and provider configuration; no verified run recorded.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
