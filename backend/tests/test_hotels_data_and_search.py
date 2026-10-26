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
    hotel_id_filename,
    hotel_id_for_offer,
    load_hotel_catalog,
    normalize_hotel_id,
    search_hotel_results,
    search_hotels,
)


def query(**overrides):
    return HotelSearch.model_validate(
        {"destination": "AYT", "check_in": "2026-11-18", "check_out": "2026-11-25", **overrides}
    )


def test_snapshot_counts_and_normalized_s4_fact_fidelity():
    catalog = load_hotel_catalog()
    assert Counter(record.destination for record in catalog) == {"AYT": 20, "DXB": 20, "MLE": 20}
    sources = json.loads((DATA_ROOT / "hotels/sources.json").read_text())["sources"]
    assert {source["destination"] for source in sources} == {"AYT", "DXB", "MLE"}
    for source in sources:
        content = (DATA_ROOT / "hotels" / source["snapshot_file"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == source["sha256"]
        assert source["license"] == "ODbL-1.0"
        assert source["attribution"] == "© OpenStreetMap contributors"
        assert source["snapshot_kind"] == "normalized_osm"
        assert source["selected_objects"] == 20
        normalized = json.loads(content)
        elements = {
            hotel["hotel_id"]: hotel for hotel in normalized["hotels"] if hotel["destination"] == source["destination"]
        }
        records = load_hotel_catalog(source["destination"])
        assert {record.hotel_id for record in records} == set(elements)
        for record in records:
            element = elements[record.hotel_id]
            assert record.name == element["name"]
            assert record.name_en == element["name_en"]
            assert (record.latitude, record.longitude) == (element["lat"], element["lon"])
            assert record.stars == element["stars"]
            assert record.source_url == element["osm_url"]
            # The normalized snapshot does not establish raw OSM metadata.
            assert record.raw_stars_tag is record.osm_version is record.osm_timestamp is None


@pytest.mark.parametrize("destination", ["AYT", "DXB", "MLE"])
async def test_every_destination_has_offers_and_explicit_synthetic_provenance(destination):
    results = await search_hotel_results(query(destination=destination))
    assert len(results) == 20
    for result in results:
        assert result.offer.hotel_id == result.hotel_id == result.offer.place.id
        assert result.offer.place.kind == "hotel"
        assert result.offer.stars == result.facts.stars
        assert result.offer.reception_24h == (result.synthetic_rules["reception_closes_at"] is None)
        assert result.offer.early_check_in_available == result.synthetic_rules["early_check_in_available"]
        assert result.offer.is_synthetic
        assert result.synthetic_rules["is_synthetic"]
        assert "синтетические" in result.notice
        assert result.facts.source_url.startswith("https://www.openstreetmap.org/")
        assert result.offer.place.latitude == result.facts.latitude
        assert hotel_id_for_offer(result.offer.offer_id) == result.hotel_id


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
        assert original[hotel_id].offer.offer_id != changed[hotel_id].offer.offer_id
        assert original[hotel_id].facts == changed[hotel_id].facts
    assert await search_hotels(query()) == await search_hotels(query())
    with pytest.raises(ValueError):
        hotel_id_for_offer("synthetic-hotel/osm:node/does-not-exist/00000000000000000000")


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
    trap = next(result for result in results if result.synthetic_rules["scenario"] == "far_transfer")
    assert trap.synthetic_rules["scenario"] == "far_transfer"
    assert trap.synthetic_rules["synthetic_airport_transfer_minutes"] == 120
    assert trap.synthetic_rules["synthetic_airport_transfer_source"] == "synthetic_scenario_override"
    assert trap.synthetic_rules["is_synthetic"]
    assert all(
        result.synthetic_rules["synthetic_airport_transfer_minutes"] is None
        for result in results
        if result.synthetic_rules["scenario"] != "far_transfer"
    )


def test_all_60_identity_aliases_resolve_without_changing_filenames_or_source_records():
    aliases = json.loads((DATA_ROOT / "hotels/id_aliases.json").read_text())["aliases"]
    records = load_hotel_catalog()
    assert len(aliases) == len(records) == 60
    assert set(aliases.values()) == {r.hotel_id for r in records}
    for legacy, canonical in aliases.items():
        assert normalize_hotel_id(legacy) == normalize_hotel_id(canonical) == canonical
        assert get_synthetic_rules(legacy) == get_synthetic_rules(canonical)
        assert get_synthetic_rules(legacy)["hotel_id"] == canonical
        filename = hotel_id_filename(canonical)
        assert filename == hotel_id_filename(legacy)
        assert "/" not in filename and ":" not in filename
    for record in records:
        assert record.hotel_id == f"osm:{record.osm_type}/{record.osm_id}"


async def test_offer_parser_accepts_both_spelling_versions_but_serializes_only_canonical_id():
    result = (await search_hotel_results(query()))[0]
    canonical = result.hotel_id
    legacy = canonical.replace("/", ":")
    digest = result.offer.offer_id.rsplit("/", 1)[1]
    assert hotel_id_for_offer(result.offer.offer_id) == canonical
    assert hotel_id_for_offer(f"synthetic-hotel/{legacy}/{digest}") == canonical
    payload = result.offer.model_dump(mode="json")
    assert payload["hotel_id"] == payload["place"]["id"] == canonical
    assert payload["offer_id"] == result.offer.offer_id
    assert "id" not in payload
    assert result.offer.id == result.offer.offer_id
    for malformed in ("osm:node/0", "osm:node/../1", "osm:hotel/1", "osm:way/not-a-number"):
        with pytest.raises(ValueError):
            normalize_hotel_id(malformed)
