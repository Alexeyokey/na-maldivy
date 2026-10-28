# Attribution for D5 destination data

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
