# D1 v0.1: перенос временных моделей S4

Изменения по [комментарию S4](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/2#issuecomment-6397086327).
Публичные модели пока являются предложением для O1; общий PR не означает согласование всех зон.

| Было в черновике | Теперь |
|---|---|
| `get_transfer -> Transfer` | `get_transfer -> list[Transfer]`, доступные по времени первыми, затем цена и ID |
| Тип физической точки не задан | Обязательный `Place.kind`: `airport`, `hotel`, `city` |
| Предложение и объект гостиницы смешивались | `HotelOffer.hotel_id` + `offer_id`, а `Place.id` гостиницы равен `hotel_id` |
| OSM ID `osm:way:123` | Канонический `osm:way/123`; старый формат нормализуется на входе |
| Неизвестные правила превращались в bool defaults | `pets`, `child_seat` и правила отеля допускают `None` |
| Только `pickup_at` | Запрошенный `pickup_at`, ближайший `depart_at`, `available`, обязательный `hours` |
| Такси/шаттл/автобус | Дополнительно `minivan`, `metro`, `boat`, `seaplane` |
| Один итоговый тариф | Итоговый тариф за группу + необязательное число машин `vehicles` |
| Собственный unsupported-route error | Общий `app.tools.errors.UnsupportedTransfer(ValueError)` |

Импорты для адаптера:

```python
from app.contracts.identifiers import canonical_hotel_id
from app.contracts.tools import Place, Travelers, Transfer, TransferHours, TransferQuery
from app.tools.errors import UnsupportedTransfer

hotel_id = canonical_hotel_id("osm:way:123456")  # osm:way/123456
hours = TransferHours.model_validate({"from": "06:00:00", "to": "18:00:00"})
```

`Transfer.hours=None` означает известную круглосуточную работу. Отсутствие расписания
в исходном API не следует превращать в это значение. Окно `[from, to)` местное;
`22:00 → 06:00` пересекает полночь. При `available=True` `depart_at` совпадает
с запросом по абсолютному времени, при `False` находится в будущем или отсутствует.
Время отправления проверяется в часовом поясе начальной точки.

Для частного транспорта `price_rub` уже учитывает число машин. У общественного
`vehicles` может быть `None`; цена всё равно является суммой за всю группу.
`available` описывает расписание отправления: неизвестные условия для питомцев
и детских кресел нужно проверять отдельно, они не становятся разрешёнными автоматически.

JSON hotel-ответа содержит `offer_id`. Старое входное имя `id` принимается для
миграции, Python `.id` доступен только для чтения; новые адаптеры должны писать `offer_id`.
Это не влияет на `Transfer.id`, `Flight.id` или `Train.id`.

Готовые примеры запросов и ответов:

- `contracts/examples/tools.json` — все шесть инструментов.
- `contracts/examples/s4-transfers.json` — Мале 23:40 → гидросамолёт на следующий день
  в 06:00; группа из пяти человек, включая младенца, → две машины такси в Анталье.
- `contracts/schema/tools.json` — входные и выходные JSON Schema.

Обновить и проверить: `make schemas tool-examples check-fast`.

Ветка `S4-transfers` и упомянутый `data/hotels/osm_hotels.json` пока не опубликованы.
Поэтому здесь исправлены публичные типы и формат идентификаторов, но не заявляется
проверка готового адаптера S4 или совпадение его 60 отелей с 72 объектами D3.
