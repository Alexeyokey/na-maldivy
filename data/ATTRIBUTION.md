# Источники данных и иллюстраций

Стандартный запуск использует включённые в репозиторий снимки и расчётные данные.
Он не требует аккаунтов поставщиков и не запрашивает их сервисы. Происхождение
данных не означает подтверждения наличия, цены покупки или права на реальную бронь.

## Отели, аэропорты и маршруты

- `hotels/osm_hotels.json`, `transfers/airports.json` и исходные дорожные данные:
  **© участники OpenStreetMap**, [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
  Названия, идентификаторы и координаты происходят из OSM. Преобразования и
  команды воспроизведения описаны в [README трансферов](transfers/README.md).
  [Условия и атрибуция OpenStreetMap](https://www.openstreetmap.org/copyright).
- `transfers/routes_osrm.json`: расчёт времени и расстояния по дорожному графу OSM
  с помощью [OSRM](https://project-osrm.org/). Время не учитывает текущие пробки.
  Лицензия программного кода OSRM не заменяет лицензию исходных данных OSM.
- `hotels/synthetic_rules.json`, цены, наличие и коммерческие правила отелей и
  трансферов — демонстрационные расчёты проекта. Они не получены от этих отелей
  или перевозчиков и не являются подтверждением их услуг.

### Вымышленное расширение 45 направлений

В `FLIGHTS_MODE=demo` исходные 60 OSM-точек дополняются 360 вымышленными
отелями из [`demo/hotels.generated.json`](demo/hotels.generated.json), по 8
для каждого из остальных 45 направлений D5. Имена, координаты, рейтинг,
удобства, коммерческие правила и наличие этих отелей — авторские
синтетические фикстуры. Идентификаторы `demo:hotel/CMB-01` и аналогичные
не являются идентификаторами OSM или реального поставщика. Они не получают
атрибуцию OSM/ODbL как факты реального отеля.

Координаты рассчитаны детерминированными смещениями от аэропортов-ориентиров
D5; происхождение самих аэропортов указано ниже. Это не геолокация настоящего
отеля. [`demo/routes.generated.json`](demo/routes.generated.json) добавляет
89 модельных маршрутных записей к исходным 6. Прямота, расписание, тарифы
и обратные плечи не подтверждают существование реального рейса.
[`demo/transfers.generated.json`](demo/transfers.generated.json) содержит
360 записей/1080 синтетических вариантов трансферов. Их расстояния —
геометрическая модель между аэропортом и вымышленной точкой, не измерения
OSRM и не маршруты перевозчика. Дополнения не меняют исходные 47 очищенных
Aviasales-ответов, OSM-каталог и дорожный снимок трёх базовых направлений.

Включение расширения даёт 48 направлений, 420 отелей и 420 записей трансферов.
Режимы `synthetic` и `aviasales` не подключают это расширение автоматически.
Воспроизведение и проверки описаны в [инвентаре](../docs/data-inventory.md)
и [карте данных](README.md).

## Сохранённые авиакотировки

`demo/aviasales/` содержит 47 очищенных ответов Aviasales Data API, сохранённых
28.10.2026. [README снимка](demo/aviasales/README.md) и `manifest.json` фиксируют
покрытие, время записи и контрольные суммы. Авторизационные данные исключены.

По [документации поставщика](https://support.travelpayouts.com/hc/en-us/articles/203956163-Aviasales-Data-API)
источник возвращает результаты из поискового кэша. В приложении это исторические
наблюдения, а не актуальное предложение покупки. Открытая лицензия на эти ответы
не заявляется; применяются условия поставщика. Этот файл фиксирует происхождение,
а не выдаёт дополнительное разрешение на коммерческое распространение снимка.

Детерминированные моки генерирует код проекта отдельно от этого снимка. Они
помечаются «ДЕМО-МОК» и не записываются как ответы Aviasales. Расчётная цена
группы, багаж и прочие доплаты сохраняют отдельные признаки происхождения.

## Курсы валют

`sources/cbr-daily.xml` и `cache/cbr/` — исторический ответ публичного сервиса
Банка России, дата действия 24.10.2026. [Источник, метаданные и ограничения](CBR-ATTRIBUTION.md).
Это справочный курс, а не доступный курс обмена или тариф банковской карты.

## Поезда и отзывы

- Стандартный режим поездов `synthetic` — расчётные сценарии Москва ↔ Петербург.
  Наличие мест, класс обслуживания и покупка билетов не подтверждены.
  [Описание модели и отдельного адаптера Яндекса](trains/README.md).
- `hotel_reviews/` — 1440 синтетических отзывов, а не тексты настоящих гостей.
  `hotel_profiles/` — 60 проверяемых профилей baseline по этим отзывам.
  `demo/hotel_reviews/` и `demo/hotel_profiles/` добавляют 8640 таких текстов
  и 360 профилей вымышленных отелей. Всего — 10 080 синтетических отзывов
  и 420 профилей, по 24 текста на отель. Лексический экстрактор сохраняет
  буквальные цитаты и согласованность с правилами детей/питомцев.
  Настоящий LLM-прогон для включённого набора не заявляется.
  [Отзывы](hotel_reviews/README.md), [профили](hotel_profiles/README.md).

## Иллюстрации интерфейса

В текущем интерфейсе используются оригинальные статические SVG проекта.
Это иллюстрации, а не фотографии конкретных отелей.
[Перечень и происхождение файлов](../frontend/dist/assets/README.md).

## Каталог направлений D5

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
