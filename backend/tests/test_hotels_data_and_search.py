"""D3 checks source fidelity, hard constraints, group pricing and stable identity."""

import hashlib
import json
from collections import Counter
from datetime import date, timedelta

import pytest

from app.contracts.tools import HotelSearch
from app.tools.hotels import (
    DATA_ROOT,
    HotelSearchTool,
    get_synthetic_rules,
    hotel_id_for_offer,
    load_hotel_catalog,
    search_hotel_results,
    search_hotels,
)


def query(**overrides):
    return HotelSearch.model_validate(
        {"destination": "AYT", "check_in": "2026-11-18", "check_out": "2026-11-25", **overrides}
    )


def test_snapshot_counts_and_raw_fact_fidelity():
    catalog = load_hotel_catalog()
    assert Counter(record.destination for record in catalog) == {"AYT": 24, "DXB": 24, "MLE": 24}
    sources = json.loads((DATA_ROOT / "hotels/sources.json").read_text())["sources"]
    for source in sources:
        content = (DATA_ROOT / "hotels" / source["raw_file"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == source["sha256"]
        assert source["license"] == "ODbL-1.0"
        assert source["attribution"] == "© OpenStreetMap contributors"
        raw = json.loads(content)
        elements = {f"osm:{e['type']}:{e['id']}": e for e in raw["elements"]}
        for record in load_hotel_catalog(source["destination"]):
            element = elements[record.hotel_id]
            assert record.name == element["tags"]["name"]
            point = element if element["type"] == "node" else element["center"]
            assert (record.latitude, record.longitude) == (point["lat"], point["lon"])
            assert record.raw_stars_tag == element["tags"].get("stars")
            if record.stars is not None:
                assert str(record.stars) == element["tags"]["stars"]


@pytest.mark.parametrize("destination", ["AYT", "DXB", "MLE"])
async def test_every_destination_has_offers_and_explicit_synthetic_provenance(destination):
    results = await search_hotel_results(query(destination=destination))
    assert len(results) >= 20
    for result in results:
        assert result.offer.is_synthetic
        assert result.synthetic_rules["is_synthetic"]
        assert "синтетические" in result.notice
        assert result.facts.source_url.startswith("https://www.openstreetmap.org/")
        assert result.offer.place.latitude == result.facts.latitude
        assert hotel_id_for_offer(result.offer.id) == result.hotel_id


async def test_hard_filters_children_pets_refund_and_unknown_stars():
    all_results = await search_hotel_results(query())
    assert any(r.offer.adults_only for r in all_results)
    assert any(not r.offer.refundable for r in all_results)
    assert any(not r.offer.pets_allowed for r in all_results)
    filtered = await search_hotel_results(
        query(
            travelers={"adults": 2, "children_ages": [5], "pets": 1},
            refundable_only=True,
        )
    )
    assert filtered
    assert all(not r.offer.adults_only and r.offer.pets_allowed and r.offer.refundable for r in filtered)
    starred = await search_hotel_results(query(min_stars=1))
    assert all(r.facts.stars is not None and r.facts.stars >= 1 for r in starred)
    assert not await search_hotels(query(max_price_rub=0))
    assert not await search_hotels(query(destination="NRT"))


async def test_group_price_rooms_nights_and_pet_surcharge_are_not_per_person():
    one = {r.hotel_id: r for r in await search_hotel_results(query(travelers={"adults": 1}))}
    three = {r.hotel_id: r for r in await search_hotel_results(query(travelers={"adults": 2, "children_ages": [5]}))}
    four = {r.hotel_id: r for r in await search_hotel_results(query(travelers={"adults": 4}))}
    pet = {r.hotel_id: r for r in await search_hotel_results(query(travelers={"adults": 1, "pets": 1}))}
    assert three and pet
    for hotel_id in three:
        assert three[hotel_id].rooms == 2
        assert three[hotel_id].offer.price_rub == 2 * one[hotel_id].offer.price_rub
        assert four[hotel_id].offer.price_rub == three[hotel_id].offer.price_rub
    for hotel_id in pet:
        assert pet[hotel_id].offer.price_rub == one[hotel_id].offer.price_rub + 1000


async def test_identity_survives_date_and_party_changes_but_offer_identifier_changes():
    original = {r.hotel_id: r for r in await search_hotel_results(query())}
    changed = {
        r.hotel_id: r
        for r in await search_hotel_results(
            query(
                check_in="2026-11-19",
                check_out="2026-11-26",
                travelers={"adults": 2},
            )
        )
    }
    shared = original.keys() & changed.keys()
    assert shared
    for hotel_id in shared:
        assert original[hotel_id].offer.id != changed[hotel_id].offer.id
        assert original[hotel_id].facts == changed[hotel_id].facts
    assert await search_hotels(query()) == await search_hotels(query())
    with pytest.raises(ValueError):
        hotel_id_for_offer("synthetic-hotel/osm:node:does-not-exist/00000000000000000000")


async def test_all_five_synthetic_traps_have_reproducible_candidates():
    rules = [get_synthetic_rules(r.hotel_id) for r in load_hotel_catalog("AYT")]
    assert {r["scenario"] for r in rules} == {
        "cheap_nonrefundable",
        "adults_only",
        "early_arrival",
        "far_transfer",
        "no_pets",
    }
    seen = set()
    for offset in range(7):
        day = date(2026, 11, 18) + timedelta(days=offset)
        results = await search_hotel_results(query(check_in=day, check_out=day + timedelta(days=7)))
        seen.update(r.synthetic_rules["scenario"] for r in results)
    assert len(seen) == 5
    tool = HotelSearchTool()
    assert tool.name == "search_hotels"
    assert tool.output_adapter.validate_python(await tool.run(query()))


async def test_required_two_hour_airport_transfer_trap_is_explicitly_synthetic():
    results = await search_hotel_results(query(travelers={"adults": 2}))
    trap = next(result for result in results if result.hotel_id == "osm:node:11725204537")
    assert trap.synthetic_rules["scenario"] == "far_transfer"
    assert trap.synthetic_rules["synthetic_airport_transfer_minutes"] == 120
    assert trap.synthetic_rules["synthetic_airport_transfer_source"] == "synthetic_scenario_override"
    assert trap.synthetic_rules["is_synthetic"]
    assert all(
        result.synthetic_rules["synthetic_airport_transfer_minutes"] is None
        for result in results
        if result.synthetic_rules["scenario"] != "far_transfer"
    )
