# Пять воспроизводимых синтетических ловушек D3

**Все коммерческие условия ниже вымышлены.** OSM предоставляет только реальное имя/точку/звёзды при наличии; ловушки не являются обвинениями или утверждениями о настоящих правилах этих отелей. Имена в API снабжены пометкой `[синтетические условия]`.

Базовый запрос: AYT, 2026-11-18 → 2026-11-25, 2 взрослых, без фильтров. Цены — вся группа за все семь ночей, включая все комнаты.

| Сценарий | OSM-якорь | hotel_id | Цена demo, ₽ |
|---|---|---|---:|
| Дешёвый невозвратный тариф | [Hotel Frankfurt](https://www.openstreetmap.org/node/530098775) | `osm:node/530098775` | 27027 |
| Adults-only при поездке с ребёнком | [Puding Marina Residence](https://www.openstreetmap.org/node/3623376904) | `osm:node/3623376904` | 32760 |
| Прилёт до заселения без раннего заезда | [Ruin Adalia Hotel](https://www.openstreetmap.org/node/10219505800) | `osm:node/10219505800` | 37170 |
| Отель в двух часах трансфера от аэропорта | [Grand Friends Butik Otel](https://www.openstreetmap.org/node/11725204537) | `osm:node/11725204537` | 40320 |
| Запрет животных при поездке с питомцем | [Alp Paşa Hotel](https://www.openstreetmap.org/node/1280250536) | `osm:node/1280250536` | 32130 |

Стабильный `hotel_id` не меняется с датами/группой; `offer.offer_id` изменяется. Цены, правила и synthetic beach distance находятся в `data/hotels/synthetic_rules.json`.

## 1. Дешёвый невозвратный тариф

Идентификатор: `osm:node/530098775`; baseline offer: `synthetic-hotel/osm:node/530098775/0156f21f1e07aa0faf30`.

В baseline `refundable=false`. Повторить тот же запрос с `refundable_only=true`: этот hotel_id должен исчезнуть. Дешевизна не отменяет жёсткое условие возвратности.

## 2. Adults-only при поездке с ребёнком

Идентификатор: `osm:node/3623376904`; baseline offer: `synthetic-hotel/osm:node/3623376904/118489e439b4b21a430a`.

В baseline `adults_only=true`. Передать travelers={"adults":2,"children_ages":[5]}. Этот hotel_id должен исчезнуть до показа пользователю. Для проверки S5 ошибочный вариант можно подать напрямую, минуя корректно работающий фильтр.

## 3. Прилёт до заселения без раннего заезда

Идентификатор: `osm:node/10219505800`; baseline offer: `synthetic-hotel/osm:node/10219505800/0184de31274a659ad518`.

Подать в тестовый таймлайн синтетическое прибытие в отель 18.11.2026 в 06:00 Europe/Istanbul. Check-in=14:00, early_check_in_price_rub=null (опция недоступна в этой synthetic схеме). S5 должен обнаружить восьмичасовой разрыв и предложить другой тариф/отель либо дополнительную ночь с пересчётом. Время прибытия не является расписанием реального рейса.

## 4. Отель в двух часах трансфера от аэропорта

Идентификатор: `osm:node/11725204537`; baseline offer: `synthetic-hotel/osm:node/11725204537/2adebe8e122352434130`.

У synthetic overlay `synthetic_airport_transfer_minutes=120`, `synthetic_airport_transfer_source="synthetic_scenario_override"`. Это намеренно заданная тестовая длительность в два часа, **не результат OSRM, не измерение маршрута и не утверждение о реальной транспортной доступности OSM-отеля**. Для сценария «не более часа от аэропорта» R2/S5 должен заметить конфликт и предложить другой вариант; S4 может получить fixture override через тестовый адаптер. В HotelSearch нет поля длительности трансфера, поэтому D3 возвращает этот вариант с явным synthetic overlay. Дополнительный synthetic beach distance 6000 м сохранён отдельно.

Точное воспроизведение после базового запроса:

```python
trap = next(r for r in results if r.hotel_id == "osm:node/11725204537")
assert trap.synthetic_rules["scenario"] == "far_transfer"
assert trap.synthetic_rules["synthetic_airport_transfer_minutes"] == 120
assert trap.synthetic_rules["synthetic_airport_transfer_source"] == "synthetic_scenario_override"
```

У остальных сценариев длительность трансфера `null` — неизвестна; она не заполняется вымышленными физическими измерениями.

## 5. Запрет животных при поездке с питомцем

Идентификатор: `osm:node/1280250536`; baseline offer: `synthetic-hotel/osm:node/1280250536/b773f8a49f6c5085070c`.

В baseline pets_allowed=false. Повторить запрос с travelers={"adults":2,"pets":1}. Этот hotel_id должен исчезнуть. Оставшиеся варианты уже включают synthetic доплату 1000 ₽ за питомца за весь срок; повторно её добавлять нельзя.

Legacy hotel_id из прежней версии принимаются через `data/hotels/id_aliases.json`; новые результаты выдают только canonical `osm:<type>/<id>`. Эти 72 точки не означают синхронизацию отдельного списка S4.

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

Runtime не использует сеть/ключи. Профили отзывов тоже synthetic и не подтверждают реальные свойства OSM-якорей.
