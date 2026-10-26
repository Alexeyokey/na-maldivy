# D3: OSM hotels and synthetic offers; D7: synthetic review profiles

These modules are independent of the agent graph and SSE. `HotelSearchTool` can be
registered after O1; it is not silently enabled in the shared registry.

## What is real and what is synthetic

`data/hotels/catalog.json` is an ODbL derivative of the saved shared S4 hotel
snapshot, `data/hotels/osm_hotels.json`. D3 and S4 use the same **60 hotels: 20 each
for AYT, DXB and MLE**, with identical physical IDs, names and coordinates.
Only the OSM object ID, name, coordinates, and the normalized 1–5 star value
retained by S4 are reference facts. Missing stars stay `null`; the original tag
is unavailable in this snapshot. These are community map
records, not verified supplier listings or current hotel star certificates.

`data/hotels/synthetic_rules.json` is a separate demonstration overlay. **Prices,
availability, ratings, amenities, room capacity, guest/pet rules, cancellation,
reception hours, early check-in, airport-transfer fixture overrides, and distance to beach are synthetic. They must
never be presented as actual conditions at the real named hotel.** In particular,
synthetic beach distance is not inferred from the real OSM coordinates.

The rich output includes both layers, provenance, and a notice. Its compatibility
`HotelOffer.name` adds `[синтетические условия]`, and `is_synthetic=true`. D1 keeps unknown OSM stars as `null`, including the compatibility offer. `min_stars > 0`
excludes unknown stars. Reception and early-check-in availability use explicit fields;
`reception_closes_at=null` alone never supplies a claim of 24-hour service.

## Shared snapshot and licensing

`scripts/fetch_hotels.py` projects the normalized S4 snapshot into the D3 format
without network requests or a second hotel selection. Its manifest,
`data/hotels/sources.json`, records the normalized input file and its SHA256.
The snapshot is not the original Overpass response: unavailable OSM versions,
timestamps and raw star tags remain `null`. Each hotel retains its OSM object link.
Maldive entries include island resorts; real coordinates do not imply that a road
connects the hotel to MLE.

The previous D3 raw responses and their manifest are archived in
`data/hotels/legacy_sources.json` and `data/hotels/osm/*.json`. Their historical
queries and hashes describe the old selection, not the active 60-hotel catalogue.

Attribution: **© OpenStreetMap contributors**. OSM snapshots and their derivative
catalogue are available under the [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
See [OpenStreetMap copyright](https://www.openstreetmap.org/copyright). Keep the
notice and licence when distributing the data; display attribution when showing
OSM-derived hotel facts. Synthetic reviews/policies are a separately identified
project-authored layer, not real supplier data or real guest reviews.

Rebuild D3 from the checked shared snapshot, entirely offline:

```bash
backend/.venv/bin/python scripts/fetch_hotels.py
# Read-only reproduction check: no file writes or network.
backend/.venv/bin/python scripts/fetch_hotels.py --check
```

`--from-snapshots` remains an alias for this offline rebuild. Refreshing the shared
source is a separate, explicit S4 operation that needs network access:

```bash
cd backend
uv run python scripts/build_transfers.py fetch
uv run python scripts/build_transfers.py build
cd ..
backend/.venv/bin/python scripts/fetch_hotels.py
backend/.venv/bin/python scripts/build_hotel_profiles.py --generate-demo-data
backend/.venv/bin/python scripts/build_hotel_profiles.py --check
```

Review the changed snapshot, OSRM routes, transfer cases and generated D3 data
together after a refresh. Offline projection does not alter the 136 saved S4
options or 40 saved OSRM routes.

## Identity, search and price scope

- `hotel_id = osm:<node|way|relation>/<id>` is a stable OSM object identity across
  dates/parties. OSM object replacement or merging can still require identity
  reconciliation; name similarity is not used as a permanent global key.
- `offer.offer_id = synthetic-hotel/<hotel_id>/<20-hex-query-digest>` identifies one
  synthetic tariff query, including dates, party, filters and tariff version.
  It is not a supplier booking token or proof of availability. `HotelOffer.hotel_id`,
  rich result `hotel_id`, and hotel `Place.id` are identical; `Place.kind="hotel"`.
  JSON emits `offer_id`; the legacy `id` input alias/property is provided by D1.
- `search_hotel_results(HotelSearch)` returns rich results including `hotel_id`,
  OSM `facts`, `synthetic_rules`, the compatible `offer`, and a synthetic profile.
- `search_hotels(HotelSearch)` returns the D1 `list[HotelOffer]` projection.
- `HotelSearchTool` implements the existing `Tool` protocol.
- `hotel_id_for_offer()` extracts a known physical-object identity from a
  well-formed demo offer ID; it does not authenticate or revalidate a booking.

Runtime uses only local files. `NA_MALDIVY_DATA_DIR` overrides the repository
`data/` path for Docker. Unsupported destinations return an empty list.

Hard filters reject adults-only with children, prohibited pets, nonrefundable
rates for a refundable-only query, insufficient/unknown stars, over-budget rates,
and deterministic synthetic inventory closures. All six tools continue using
D1 contracts; no shared TripState or event schema is introduced here.

Price is integer RUB **for all rooms, all nights and the entire party**. Two
people fit a synthetic room, every child counts as a full place, and rooms are
rounded up. A deterministic month factor scales the base room/night rate. Pet
surcharge is once per pet per stay and already included. Do not multiply the
result by nights or passengers again. Early check-in is a separate optional fee,
calculated for all rooms; it is not silently added to the stay total.

Five reproducible examples are in `data/traps.md`, including an explicitly synthetic 120-minute airport transfer. The transfer override is a separate test fixture, not a physical route measurement; actual S3/S5 timelines use the selected S4 route. Other D3 overlay transfer times remain unknown. Their commercial rules are
synthetic even when the hotel anchor is a real OSM object.

## D7 corpus and checked profile pipeline

There are **24 synthetic reviews per hotel**, six each for family, couple,
business and pet guest segments: **1,440 reviews and 60 checked profiles**.
Every text starts with a synthetic marker.
They are project-authored deterministic templates, not scraped reviews and not
evidence about the named hotel. Saved review files include generation provenance
and reference annotations. No personal data or real reviewers are used.

The checked profiles use `lexical-fixture-baseline-v1`, a transparent extractor
that reads the text and matches the documented synthetic phrase vocabulary.
It does not use the annotation labels during extraction. The builder separately
compares extracted mentions against fixture annotations. This proves the fixture
pipeline and exact evidence accounting; it is not a measured general-language
review sentiment benchmark.

Profiles include eight aspects (cleanliness, noise, food, staff, children, wifi, location, pets), segment-specific pros/cons, positive/negative
mention counts, distinct review counts, and every contributing literal quote
with its review ID. Aggregation validates complete review coverage and rejects
invented quotes, duplicate evidence, mismatched hotel IDs and changed segments.
The saved metadata states `model=null` and `live_model_run=false`.

Regenerate synthetic overlay/corpus and profiles, entirely offline:

```bash
backend/.venv/bin/python scripts/build_hotel_profiles.py --generate-demo-data
```

Rebuild profiles from the checked corpus, entirely offline:

```bash
backend/.venv/bin/python scripts/build_hotel_profiles.py
# Read-only reproduction check (no file writes and no network):
backend/.venv/bin/python scripts/build_hotel_profiles.py --check
```

## Optional LLM extraction (prepared, not run against a live model)

`extract_reviews_llm(client, reviews)` uses the existing `LLMClient.complete` with
JSON mode and the fast tier. Review text is labelled as untrusted data. The model
supplies per-review aspect/sentiment/literal quote, while code validates the output
and calculates counts. Model aggregate counts are never accepted as evidence.
Its interface is checked using injected `FakeLLMClient` responses without keys.

No live model extraction has been run for the checked profiles. Running it needs
an explicitly configured non-fake provider and a deliberate command:

```bash
backend/.venv/bin/python scripts/build_hotel_profiles.py \
  --mode llm --allow-live-llm --hotel-id 'osm:node/REPLACE_WITH_KNOWN_ID'
```

This optional command may use the configured provider and incur its charges.
Omitting `--allow-live-llm` fails before creating/contacting a provider. The default
pipeline does not load `.env` or create an LLM client.

## Remaining integration boundaries

O1 should agree nullable star/rule fields, stable `hotel_id` versus offer ID,
commercial-field provenance, and how profiles attach to ranking facts. D3 data
and independent search are usable now; A2/R2 integration remains separate. D7's
live-model extraction and assessment on non-template data remain unverified.

## Canonical catalogue and ID migration

The active D3 catalogue follows the 60 saved S4 objects. `data/hotels/id_aliases.json`
maps legacy spellings of active IDs, such as
`osm:node:123`/`osm:way:123`/`osm:relation:123`, to the canonical
`osm:node/123`/`osm:way/123`/`osm:relation/123`. `normalize_hotel_id`,
`get_synthetic_rules`, `get_hotel_profile`, and `hotel_id_for_offer` accept known
legacy lookup IDs but return canonical identities. New catalogue, rule, review,
profile, place and offer outputs use canonical IDs only. Hotels removed with the
old D3 selection are not reassigned to another physical hotel.
The compatibility suite checks all 60 through actual U2 offers, S3 timelines and
S5 rules, using day and night flights for each destination.
The synthetic D3 `far_transfer` override is not substituted for an S4 route duration.

Review/profile filenames remain flat, e.g. `osm-node-123.json`: both `:` and `/`
are escaped as `-`. Offer parsing separates the final digest using `rsplit("/",1)`
because the canonical hotel ID itself contains `/`. Archived raw OSM snapshots
and their hashes remain unchanged. Synthetic rules, reviews and profiles are
regenerated for the active catalogue; their prices and policies are demo fixtures,
not a promise to preserve the previous 72-hotel selection's commercial values.
Their tariff version is `synthetic-hotel-v2`, so regenerated offers get new IDs
even when a physical hotel and query are unchanged.

Offline regeneration after identity/contract changes:

```bash
backend/.venv/bin/python scripts/fetch_hotels.py --from-snapshots
backend/.venv/bin/python scripts/build_hotel_profiles.py --generate-demo-data
backend/.venv/bin/python scripts/fetch_hotels.py --check
backend/.venv/bin/python scripts/build_hotel_profiles.py --check
make u2-compat-check
```
