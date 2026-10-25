# D2/D6: transport adapters and reproducible demo data

The code implements the draft D1 `FlightSearch → list[Flight]` and
`TrainSearch → list[Train]` contracts. It does not change that draft, agent
architecture, or the web interface. All returned demo offers have
`is_synthetic=true`.

## What is sourced, and what is modelled

| Layer | Evidence/status |
| --- | --- |
| Airline route network | Six route pairs with carrier/airport source URLs in `data/routes/network.json`, checked 26 October 2026. Some SVO pages are historical flights whose content was available in the official search index; their current dynamic page may show only an airport shell. They establish an observed route, not current operation or availability for an arbitrary date. |
| Airports | Rounded coordinates and IANA zones inherited from the D1 draft. These are gateway points, not a geographic survey. |
| Demo flight dates, times, durations, fares, baggage and pet fees | Explicit synthetic model. The seed, route, date and variant determine a stable result. Reversing a route for a return trip is also a modelled demo behaviour. |
| Aviasales provider quotes | Client implemented against the official Data API schema. The API returns cached user-search observations, not confirmed seats, group inventory or a live booking quote. No real API call using a partner token has been made. |
| Yandex train timetable | Client implemented against official API v3.0, including pagination, UTC offsets and elapsed duration validation. No real keyed response is bundled. |
| Train fare | Always a synthetic distance × class × season × group model. Requested wagon class is a model input; actual class/seat/pet availability is unknown. |
| JSON examples under `data/routes` / `data/trains` | Handwritten provider-shaped test fixtures. Their envelope explicitly says `kind=synthetic` and `is_captured_provider_response=false`. They are not HTTP recordings. |

The network supports SVO (MOW alias) and LED to AYT, DXB and MLE, and modelled
reverse routes. **LED–MLE has one connection via DXB**; `direct_only=true`
returns no offer for that route. AYT/DXB/MLE are different airport identifiers;
DXB is never silently substituted with DWC.

Source pages:

- [SVO–AYT, official Sheremetyevo SU2144 page](https://www.svo.aero/ru/timetable/departure/flight/9644648/info)
- [SVO–DXB, official Sheremetyevo SU520 page](https://www.svo.aero/en/timetable/departure/flight/9694787)
- [SVO–MLE, official Sheremetyevo SU320 page](https://www.svo.aero/en/timetable/departure/flight/9561197/info)
- [LED–AYT, official Pulkovo schedule](https://pulkovoairport.ru/passengers/destinations/flight_schedule/?city=%D0%90%D0%BD%D1%82%D0%B0%D0%BB%D1%8C%D1%8F)
- [LED–DXB, Emirates route](https://www.emirates.com/ru/english/destinations/led/dxb/flights-from-saint-petersburg-to-dubai/)
- [LED–MLE, Emirates itinerary](https://www.emirates.com/ru/english/destinations/led/mle/flights-from-saint-petersburg-to-male/)
- [DXB–MLE, Emirates route](https://www.emirates.com/ae/english/destinations/dxb/mle/flights-from-dubai-to-male/)

Only route facts are retained from these pages. Website text, logos and actual
prices are not redistributed as an open licensed dataset.

## Flight API

```python
from datetime import date
from app.contracts.tools import FlightSearch, Travelers
from app.tools.flights import FlightTool, generate_flights

query = FlightSearch(
    origin="SVO", destination="DXB", departure_date=date(2030, 11, 18),
    travelers=Travelers(adults=2, children_ages=[5]),
)
flights = generate_flights(query, seed=42)
flights_again = await FlightTool(mode="synthetic", seed=42).run(query)
assert flights == flights_again
```

`generate_flights(query, seed=42)` is the single pricing source for both flight
search and D8's demo price calendar. It uses SHA-256-derived integers rather
than Python's process-randomised `hash()` or global random state. Calling it in
another order or process does not change the result. A separate returned list
and fresh DTOs protect subsequent calls from caller mutations.

Each route offers a 09:00 and a 23:30 local departure. Durations and per-person
fares vary reproducibly by date and seed. July, August and December apply a
synthetic 1.2 seasonal multiplier. All people, including children, occupy a
seat at the modelled fare; pets add 1,500 RUB each. `price_rub` already includes
the entire group for one direction. `baggage_price_rub` is a separate 2,000 RUB
per person; it is not included twice. `max_price_rub` filters the group fare.

The late departure is a deliberate date-change test. Departure is constructed
with the origin's `ZoneInfo`; duration is added in UTC; arrival is converted to
the destination's `ZoneInfo`. Crossing midnight/year-end, DST offsets and
overflow at `date.max` are handled without adding travel duration to wall-clock
time. `is_overnight` keeps D1's current definition: destination local arrival
date is later than origin local departure date. This is not a general
classification of every flight that touches night hours.

### Aviasales Data API

[Official Data API documentation](https://support.travelpayouts.com/hc/en-us/articles/203956163-Aviasales-Data-API)
specifies `GET https://api.travelpayouts.com/aviasales/v3/prices_for_dates`.
The client sends origin/destination, exact departure date, `one_way=true`,
`direct`, `currency=rub`, `market=ru`, sorting and pagination. Credentials go
only in `X-Access-Token`. The returned object must contain `success=true` and a
list of quotes. Round-trip quotes, naive timestamps, malformed values and
non-RUB responses are rejected. Quotes from a different airport or local date
are excluded instead of relabelled as the requested journey.

```python
from app.tools.http_cache import CachedHttpClient

async with CachedHttpClient(mode="record", cache_dir=cache_dir) as http:
    tool = FlightTool(mode="provider", http=http, token=travelpayouts_token)
    result = await tool.search_with_provenance(query)
    # result.quotes: unmultiplied one-seat provider observations
    # result.flights: D1 DTOs, is_synthetic=True
    # result.provenance: exact quote and derivation for every DTO
```

Provider-backed DTOs retain `is_synthetic=true`: arrival may be derived from
cached `duration_to` or a synthetic route estimate, and group/extras are
modelled. Multiplication of a one-seat quote is an estimate, not a promise that
all passengers can buy at the same price. Original quote values remain in
`FlightSearchResult.quotes` and in per-offer provenance.

No partner key is needed for **replay of an already recorded response** because
D4 omits credentials from cache identity. A replay miss raises `CacheMiss`.
Live/record without a token raises `ProviderNotConfigured`; provider/HTTP
errors propagate. There is no automatic switch to invented live prices.

## Train API

`generate_trains(TrainSearch(...), seed=42)` returns a 23:55 night demo for
MOW↔LED; seated class also has a 09:00 fast daytime demo. Sleeper classes are
not assigned to this fast service. Named Sapsan/Lastochka provider records are
also excluded when the requested class is not seated. SVO maps to the Moscow city alias only for this
train contract. Returned coordinates refer to mapped rail gateways rather than
airports. The first adapter supports Moscow Leningradsky `s2006004` and
Saint Petersburg Moskovsky `s2004001`; other stations and transfers are skipped
instead of assigned fabricated station coordinates.

`train_price(query, distance_km=None, seed=42)` uses the settings in
`data/trains/model.json`: modelled distance 650 km; class rates 4, 5, 8 and
16 RUB/km for seated, platzkart, coupe and SV respectively; summer multiplier
1.25; 25 December–10 January multiplier 1.35; stable seed/route adjustment;
500 RUB per pet. Every person occupies one modelled seat. The 650 km distance
is explicitly an illustrative input, not a measured routing result.

[Official Yandex point-to-point documentation](https://yandex.ru/dev/rasp/doc/ru/reference/schedule-point-point)
defines `GET https://api.rasp.yandex-net.ru/v3.0/search/`. The client uses city
codes `c146` and `c213`, `transport_types=train`, exact date, no transfers,
and `limit`/`offset` pagination. The key is sent as `Authorization`, never in
the URL. The new `yandex-net.ru` hostname follows the current documentation.

`TrainTool(mode="provider", http=http, key=yandex_key)` normalises the
offset-aware departure/arrival timestamps to the station zones and verifies
their elapsed duration against the provider's seconds. Missing timezone,
inconsistent duration, malformed response, exhausted pagination and HTTP errors
raise an explicit error. Empty matches return an empty list. `TrainSearchResult`
also supplies per-offer provenance; timetable is attributed to the provider and
price/class availability are clearly distinguished. No tariff is inferred from
an empty `tickets_info` field.

## Validation and remaining integration

Run the focused checks from `backend`:

```sh
.venv/bin/pytest tests/test_flights.py tests/test_trains.py -q
```

Tests use `httpx.MockTransport` with explicitly synthetic fixture payloads.
They exercise both a provider-shaped response and D4 record→replay without
credentials, rather than merely calling the generators twice. Other checks
cover group/extras, budget boundaries, the unsupported direct LED–MLE route,
reverse routes, a night at year-end, DST elapsed duration, train wagon classes,
distance/season pricing, pagination, malformed data and missing credentials.

Real partner credentials, genuine HTTP recordings and actual live-provider
availability have **not** been tested. Keyed verification remains a separate
integration step. Contract questions from O1 (city/airport identity, unknown
baggage/pet policies, error envelopes and itinerary segments) remain open;
the adapters preserve the existing draft fields and expose richer provenance
alongside them.
