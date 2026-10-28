# Подключение интерфейса к сборке без модели

Сборка запускается `make demo-up`: интерфейс на localhost:5173,
HTTP API доступен через localhost:18083.
Агент находится во внутренней сети Docker; входной nginx передаёт API/SSE без
буферизации. Все источники в демо — replay/локальные данные и явно подписанные моки, ключи пустые.
У каждого рейса `demo_source.kind=provider_replay|deterministic_mock`; показывайте
пометку мока отдельно по плечам. `price_source` Aviasales есть только у настоящей записи.
Если D8 содержит `savings_kind=demonstration_estimate`, подпишите экономию как
расчётную демонстрацию, а не настоящее предложение Aviasales.
Это готовая граница для интерфейса, не утверждение о завершении U1/S2/U5.

## Чат и карточки

`POST /api/chat`: `{message, session_id?}` → SSE. Сохраните `session_id` из `done`.
События `delta` дописывают текст, `question` задаёт уточнение, `options` заменяет
набор карточек своего `kind`; `done` завершает ход, `error` — сбой. Новые
неизвестные события клиент может пропускать, не прерывая поток.

Вариант содержит `total_rub: int | null`, `known_total_rub`, `price_scope`,
`unpriced_components`, `can_confirm`, `issues`, `timeline`. Нельзя форматировать
`null` как число или называть известную сумму полной ценой отпуска. Показывайте
«за показанные компоненты» и список неоценённых расходов.

`can_confirm=false` блокирует оформление. Показанные предупреждения остаются
важными даже у разрешённого демо-варианта: наличие билетов и номера не подтверждено.
`Flight.price_source` отдельно хранит исходную цену места, режим кэша и дату записи;
`is_synthetic=true` у расчётного Flight не отменяет настоящего источника этой цены.

`timeline.events[].at` — местное время с UTC-смещением; `timezone` — IANA-зона.
Не применяйте браузерный пояс к уже отформатированному местному времени без подписи.
`hotel_arrival_at` и `booked_check_in` различаются; неизвестный check-in остаётся `null`.

## Новые факты

| Событие | Полезные поля | Что показать |
|---|---|---|
| `date_savings` | `comparison_status`, `baseline_known_total_rub`, `checked_dates`, `suggestions`, `price_scope`, `unpriced_components` | Точную разницу только по сопоставимым компонентам. `unavailable` не означает отсутствие экономии. |
| `hotel_profile` | `status`, `segment`, `counts`, `evidence`, `is_synthetic`, `extraction` | Числа и цитаты с `review_id`; пометка синтетических отзывов обязательна. |
| `destination_info` | `as_of`, `source_urls`, `visa`, `visa_period_valid`, `full_trip_price_rub` | Датированную справку и ограничения действия правила. Не обещать будущий въезд. |
| `data_integrity` | `status=refused_fabrication`, `state_changed=false` | Ответ с отказом выдумывать цену/наличие. |

Сравнение дат не переносит поездку: после `date_savings` выбранные даты,
вариант, версия и бронирование остаются прежними. Для изменения нужен явный
запрос «Перенеси на два дня позже». Старую корзину после изменения оплатить нельзя.

## Оформление

1. Выбор варианта переводит чат в `awaiting_confirmation`; оплаты ещё нет.
2. Явное «Да, оформляй» создаёт корзину: `booking.status=awaiting_travelers`,
   `paid=false`, этап `checkout_pending`. Используйте `cart_id` из события.
3. `PUT /api/checkout/carts/{id}/travelers`: `session_id`, `travelers` с
   вымышленными `full_name` и `birth_date`. Бриф определяет число и возраст людей.
4. Покажите сумму, состав, неоценённые доплаты и предупреждение о демо.
   `POST /api/checkout/carts/{id}/pay` принимает `outcome=approved|declined`,
   новый `idempotency_key`, `acknowledge_demo=true`, `acknowledge_unpriced=true`,
   `session_id`. Полей банковской карты нет.
5. Только `status=paid_demo` означает успешную имитацию. При отказе остаётся
   возможность повторить с новым ключом. Тот же ключ возвращает тот же результат.
6. Заказ, HTML-ваучер и `.ics` доступны по `GET /api/checkout/orders/{id}`,
   `/voucher`, `/calendar.ics` с заголовком `X-Session-ID`.

Для скачивания из браузера используйте `fetch` с этим заголовком и `Blob`;
идентификатор доступа не добавляйте в query. API отвечает `Cache-Control: no-store`.
Ваучер — HTML, не PDF. Покажите «демо: деньги не списаны, билеты не выписаны».
После перезапуска сессия чата исчезнет; законченный заказ сохранится.

Проверенный сценарий и границы готовности — [demo.md](demo.md), контракт
оформления — [demo-checkout.md](demo-checkout.md). Для приёмки интерфейса нужен
собственный полный проход с пятью записями в `eval/demo-runs.json`.
