# D3: OSM hotels and synthetic offers; D7: synthetic review profiles

These modules are independent of the agent graph and SSE. `HotelSearchTool` can be
registered after O1; it is not silently enabled in the shared registry.

## What is real and what is synthetic

`data/hotels/catalog.json` is an ODbL derivative of saved OpenStreetMap snapshots.
Only the OSM object ID, name, coordinates, and a 1–5 `stars` tag when present are
reference facts. Missing/malformed stars stay `null`. These are community map
records, not verified supplier listings or current hotel star certificates.

`data/hotels/synthetic_rules.json` is a separate demonstration overlay. **Prices,
availability, ratings, amenities, room capacity, guest/pet rules, cancellation,
reception hours, early check-in, airport-transfer fixture overrides, and distance to beach are synthetic. They must
never be presented as actual conditions at the real named hotel.** In particular,
synthetic beach distance is not inferred from the real OSM coordinates.

The rich output includes both layers, provenance, and a notice. Its compatibility
`HotelOffer.name` adds `[синтетические условия]`, and `is_synthetic=true`. D1 v0 has
no nullable stars, so this projection maps unknown OSM stars to `0`; this means
“unknown in this adapter”, not a certified zero-star category. `min_stars > 0`
excludes unknown stars. The richer `facts.stars` remains `null`.

## Snapshot regions and licensing

The snapshot query is `tourism=hotel` with a nonempty `name`. It contains nodes,
ways, and relations; way/relation coordinates are bounding-box centres, explicitly
marked as such. Selection is the nearest 24 distinct named objects to each saved
catchment centre, deduplicating equal names within 150 metres.

- AYT: 25 km from Antalya city centre, serving as a hotel catchment for this demo.
- DXB: central Dubai bbox south 25.14, west 55.20, north 25.28, east 55.34.
- MLE: Male/Hulhumale bbox south 4.15, west 73.48, north 4.25, east 73.56.
  This does not represent all Maldives resorts. A hotel may need a boat transfer;
  its presence must not be treated as proof of road connectivity.

`data/hotels/sources.json` records the exact query, request URL, endpoint, fetched
UTC time, OSM base timestamp, raw snapshot SHA256, region, and object counts.
`data/hotels/osm/*.json` retain the source responses. Every selected record links
to its OSM object and retains version/timestamp when returned.

Attribution: **© OpenStreetMap contributors**. OSM snapshots and their derivative
catalogue are available under the [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
See [OpenStreetMap copyright](https://www.openstreetmap.org/copyright). Keep the
notice and licence when distributing the data; display attribution when showing
OSM-derived hotel facts. Synthetic reviews/policies are a separately identified
project-authored layer, not real supplier data or real guest reviews.

Refresh (network access, read-only public API):

```bash
backend/.venv/bin/python scripts/fetch_hotels.py
```

Rebuild from the exact checked snapshots without network:

```bash
backend/.venv/bin/python scripts/fetch_hotels.py --from-snapshots
```

## Identity, search and price scope

- `hotel_id = osm:<node|way|relation>:<id>` is a stable OSM object identity across
  dates/parties. OSM object replacement or merging can still require identity
  reconciliation; name similarity is not used as a permanent global key.
- `offer.id = synthetic-hotel/<hotel_id>/<20-hex-query-digest>` identifies one
  synthetic tariff query, including dates, party, filters and tariff version.
  It is not a supplier booking token or proof of availability.
- `search_hotel_results(HotelSearch)` returns rich results including `hotel_id`,
  OSM `facts`, `synthetic_rules`, the compatible `offer`, and a synthetic profile.
- `search_hotels(HotelSearch)` returns the D1 v0 `list[HotelOffer]` projection.
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

Five reproducible examples are in `data/traps.md`, including an explicitly synthetic 120-minute airport transfer. The transfer override is a test fixture for S4/S5, not a physical route measurement; other hotel transfer times remain unknown. Their commercial rules are
synthetic even when the hotel anchor is a real OSM object.

## D7 corpus and checked profile pipeline

There are **24 synthetic reviews per hotel**, six each for family, couple,
business and pet guest segments. Every text starts with a synthetic marker.
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
  --mode llm --allow-live-llm --hotel-id 'osm:node:REPLACE_WITH_KNOWN_ID'
```

This optional command may use the configured provider and incur its charges.
Omitting `--allow-live-llm` fails before creating/contacting a provider. The default
pipeline does not load `.env` or create an LLM client.

## Remaining integration boundaries

O1 should agree nullable star/rule fields, stable `hotel_id` versus offer ID,
commercial-field provenance, and how profiles attach to ranking facts. D3 data
and independent search are usable now; A2/R2 integration remains separate. D7's
live-model extraction and assessment on non-template data remain unverified.
