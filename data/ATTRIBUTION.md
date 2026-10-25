# Attribution for U2 travel data

The catalogue is a derived compilation. Its metadata records provenance for each data group.

- **Airport reference data:** mwgg/Airports, commit
  `a89e4e0dda9d9878337e2da190ebe1ef1d6af705`, MIT license.
  The original copyright and permission notice is included in
  [sources/airports-MIT-LICENSE.txt](sources/airports-MIT-LICENSE.txt).
  Source: <https://github.com/mwgg/Airports>.
  Changes: selected 48 destination airports and two departure anchors, renamed fields,
  and added editorial destination names, tags and aliases.
- **Meteorological climatology:** NASA POWER, MERRA-2, 1991–2020, T2M and PRECTOTCORR.
  Source: <https://power.larc.nasa.gov/>.
  License: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/),
  as listed by the provider at <https://registry.opendata.aws/nasa-power/>.
  Changes: selected airport grid points, converted mean daily precipitation to mean monthly
  precipitation using Gregorian month lengths over the baseline, rounded values and embedded
  the monthly results. This does not imply NASA endorsement.
- **Sea surface climatology:** data provided by NOAA PSL, Boulder, Colorado, USA,
  from their website at <https://psl.noaa.gov>. NOAA OISST v2.1 monthly means, 1991–2020.
  Source: <https://downloads.psl.noaa.gov/Datasets/noaa.oisst.v2.highres/sst.mon.ltm.1991-2020.nc>.
  Changes: selected the nearest valid ocean cell to each beach destination, rounded monthly
  temperatures and retained the cell coordinates and distance. NOAA requests acknowledgement;
  see <https://psl.noaa.gov/data/gridded/data.noaa.oisst.v2.highres.html>.
- **Visa reference facts:** manually checked official government/embassy sources, linked
  in the catalogue and `sources/visas.json`. Facts apply to the stated passport, purpose and
  snapshot date; unknown facts remain unknown. Official source text is not reproduced in full.

All climatological values describe grid cells and historical monthly averages, not a forecast.
Editorial tags and estimated flight times are project calculations; they are not supplier claims.

## Hotel and transport additions

- **Hotel reference database:** © OpenStreetMap contributors, licensed under
  [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
  OSM raw snapshots, query URLs and hashes: `hotels/osm/` and `hotels/sources.json`.
  Derived reference facts: `hotels/catalog.json`, also ODbL 1.0.
  See [OSM copyright and contributors](https://www.openstreetmap.org/copyright).
  Commercial overlays and review texts are separate, explicitly synthetic project fixtures.
- **Bank of Russia:** official daily reference-rate XML, source
  <https://www.cbr.ru/scripts/XML_daily.asp>, format
  <https://www.cbr.ru/development/SXML/>. Exact request, hash and effective date:
  `sources/cbr-provenance.json`. These are reference rates, not exchange offers.
- **Route reference facts:** manually paraphrased primary airline/airport pages,
  linked per route in `routes/network.json`. Only brief route facts are retained;
  source articles, logos and photographs are not redistributed.
- **Provider-shaped transport fixtures:** `routes/aviasales_prices.synthetic.json`
  and `trains/yandex_search.synthetic.json`
  are project-authored synthetic protocol examples, not real API recordings.
  Partner credentials were not used to create them.
