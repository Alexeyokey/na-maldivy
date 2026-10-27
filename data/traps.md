# Пять воспроизводимых синтетических ловушек D3

**Все коммерческие условия ниже вымышлены.** Имена, координаты и нормализованные звёзды взяты из сохранённого OSM-каталога S4. Ловушки не описывают настоящие правила этих отелей. Имена предложений в API снабжены пометкой `[синтетические условия]`.

Базовый запрос: AYT, 2026-11-18 → 2026-11-25, двое взрослых, без фильтров. Цена — вся группа за семь ночей и все комнаты, тариф `synthetic-hotel-v3`.

| Сценарий | OSM-якорь | hotel_id | Цена demo, ₽ |
|---|---|---|---:|
| Дешёвый невозвратный тариф | [Sky Business Hotel](https://www.openstreetmap.org/node/5369319975) | `osm:node/5369319975` | 23338 |
| Adults-only при поездке с ребёнком | [Miramare Queen Hotel](https://www.openstreetmap.org/way/433249157) | `osm:way/433249157` | 37100 |
| Прилёт до заселения без раннего заезда | [Limak Arcadia Sport Resort](https://www.openstreetmap.org/node/2266976150) | `osm:node/2266976150` | 32760 |
| Фикстура двухчасового трансфера | [Puding Marina Residence](https://www.openstreetmap.org/node/3623376904) | `osm:node/3623376904` | 32760 |
| Запрет животных при поездке с питомцем | [Temizotel](https://www.openstreetmap.org/way/74016732) | `osm:way/74016732` | 42000 |

Все пять объектов входят в общий список D3/S4: 60 отелей, по 20 AYT/DXB/MLE.
`hotel_id` сохраняет физическую идентичность при изменении дат/группы; `offer_id`
зависит от запроса и версии тарифа. Правила находятся в `data/hotels/synthetic_rules.json`.

## 1. Дешёвый невозвратный тариф

`osm:node/5369319975`: в базовом запросе `refundable=false`. При `refundable_only=true`
этот отель исчезает из выдачи. Дешевизна не отменяет жёсткое требование возвратности.

## 2. Adults-only при поездке с ребёнком

`osm:way/433249157`: в базовом запросе `adults_only=true`. При
`travelers={"adults":2,"children_ages":[5]}` отель исчезает до показа пользователю.
Для отдельной проверки S5 ошибочный вариант подают напрямую, минуя фильтр D3.

## 3. Прилёт до заселения без раннего заезда

`osm:node/2266976150`: подать в тестовый таймлайн прибытие в отель 18.11.2026
в 06:00 Europe/Istanbul. Заселение с 14:00, ранний заезд недоступен,
`early_check_in_price_rub=null`. S5 обнаруживает восьмичасовой разрыв и предлагает
другой отель либо дополнительную ночь с пересчётом. Это отдельное тестовое
прибытие, а не расписание реального рейса.

## 4. Фикстура двухчасового трансфера

`osm:node/3623376904`: в D3 overlay стоят
`synthetic_airport_transfer_minutes=120` и
`synthetic_airport_transfer_source="synthetic_scenario_override"`.
Это тестовая длительность, не результат OSRM и не физическое измерение маршрута.
Synthetic beach distance 6000 м также вымышлена.

```python
trap = next(r for r in results if r.hotel_id == "osm:node/3623376904")
assert trap.synthetic_rules["scenario"] == "far_transfer"
assert trap.synthetic_rules["synthetic_airport_transfer_minutes"] == 120
assert trap.synthetic_rules["synthetic_airport_transfer_source"] == "synthetic_scenario_override"
```

Опубликованный S4 не принимает эту подмену. При проверке настоящего выхода D3
таймлайн S3 и валидатор S5 получают длительность выбранного варианта S4.
Двухчасовой конфликт с ограничением «не более часа» воспроизводят отдельным
тестовым `TransferLeg`, как в `test_validator.py`.
У остальных D3-сценариев overlay-длительность трансфера остаётся `null`;
доступные маршруты запрашиваются отдельно у S4.

## 5. Запрет животных при поездке с питомцем

`osm:way/74016732`: в базовом запросе `pets_allowed=false`. При
`travelers={"adults":2,"pets":1}` отель исчезает из выдачи.
Цена оставшихся подходящих предложений уже включает синтетическую доплату
1000 ₽ за питомца за весь срок; повторно её добавлять нельзя.

## Как воспроизвести

```python
from app.contracts.tools import HotelSearch
from app.tools.hotels import search_hotel_results

query = HotelSearch(
    destination="AYT", check_in="2026-11-18", check_out="2026-11-25",
    travelers={"adults": 2},
)
results = await search_hotel_results(query)
for result in results:
    print(result.hotel_id, result.synthetic_rules["scenario"], result.offer.price_rub)
```

Legacy colon-ID активных отелей принимаются через `data/hotels/id_aliases.json`;
новые результаты используют `osm:<type>/<id>`. Удалённые из активного списка
объекты не переназначаются другим отелям. Runtime не использует сеть или ключи.
Профили отзывов также синтетические и не подтверждают реальные свойства OSM-якорей.
