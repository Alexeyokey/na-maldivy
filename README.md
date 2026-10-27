# na-maldivy · О!дин запрос

Репозиторий команды «На Мальдивы» на Ostrovok Hackathon 2026.

Пока здесь запуск, план работ и порядок взятия задач. Полное описание архитектуры появится по задаче [#34](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/34) A9, тогда план переедет в `CONTRIBUTING.md`.

## Запуск

Нужен только Docker. Без `.env` агент работает на фейковой модели, которая повторяет сообщение. Этого хватает, чтобы собирать интерфейс и инструменты без ключей.

```bash
cp .env.example .env        # необязательно: ключ и модели LLM
docker compose up --build   # агент на http://localhost:8000, документация API на /docs
curl localhost:8000/health
curl -N localhost:8000/api/chat -H 'Content-Type: application/json' -d '{"message": "Хочу на море в ноябре"}'
```

`POST /api/chat` отвечает потоком SSE: события `delta` с кусками текста `{"text": "..."}`, в конце `done` или `error` с `{"message": "..."}`. Формат временный, до контрактов O1.

**Настоящая модель.** Подходит любой OpenAI-совместимый API: OpenAI, OpenRouter, YandexGPT, vLLM. В `.env` задайте `LLM_PROVIDER=openai`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_MAIN` и при желании `LLM_MODEL_FAST`.

**LLM-клиент в коде** лежит в `backend/app/llm`: `complete()` возвращает ответ целиком, вместе с вызовами инструментов и `json_mode`; `stream()` отдаёт текст по кускам; `tier="fast"` выбирает быструю модель. В тестах используйте `FakeLLMClient` с заготовленными ответами, сеть не нужна. Любая ошибка провайдера (таймаут, сеть, 5xx, битый ответ) приходит как `LLMError`.

**Инструменты D1 v0.1.** Контракты шести инструментов лежат в `backend/app/contracts/tools.py`,
заглушки и реестр — в `backend/app/tools`. Они работают без сети и ключей, по умолчанию `TOOLS_MODE=stub`.
Примеры, смысл цен, JSON Schema и список решений для согласования — в [docs/tool-contracts.md](docs/tool-contracts.md).
Подключение инструментов к диалогу появится в A2.
Для опубликованной базы S4 есть `S4TransferTool`: аэропорт и ID отеля разрешаются на сервере,
а результат передаётся в S3/S5 через `transfer_leg()`. Инструкция — в
[docs/d1-s4-migration.md](docs/d1-s4-migration.md). Остальные решения TripState и событий интерфейса остаются O1.

**Бэкенд без Docker** (нужен [uv](https://docs.astral.sh/uv/)):

```bash
cd backend
uv sync
uv run uvicorn app.main:create_app --factory --reload
uv run pytest
uv run ruff check . && uv run ruff format .
```

То же самое через `make dev`, `make test`, `make lint`, `make fmt`.

**Проверки перед PR.** Actions в организации хакатона не оплачены, поэтому автоматический CI выключен, а проверки запускаем у себя:

```bash
make check        # линтер, тесты, docker compose и смоук /health и /api/chat
make check-fast   # то же без Docker
```

Без `make` то же самое делает `bash scripts/check.sh`. Смоук поднимается отдельным проектом на порту 18000, так что уже запущенный `make up` он не трогает. Если Actions заработают, верните триггеры в `.github/workflows/ci.yml`: workflow вызывает тот же скрипт.

## Задачи и метки

Все 47 задач лежат в [Issues](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues). У каждой задачи три метки: приоритет, день и зона.

| Метка | Что значит |
|---|---|
| `P0` | Без этого нет демо |
| `P1` | Усиливает решение |
| `P2` | Режем первым, если не успеваем |
| `Д1` … `Д7` | День, к концу которого задача должна быть закрыта |
| `У1` … `У5` | Зона ответственности, см. ниже |
| `общая` | Задача всей команды, внутри чек-лист по зонам |
| `в работе` | Кто-то уже делает эту задачу |

## Зоны

Пять зон, по одной на человека. Кто какую зону берёт, решаем на встрече по контрактам [#3](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/3) O1 и вписываем логины в таблицу.

| Зона | Кто | Объём |
|---|---|---|
| У1 Агент и диалог | | 63 ч |
| У2 Данные и поиск | | 63 ч |
| У3 Ранжирование и пересборка | | 63 ч |
| У4 Сборка и покупка | | 63 ч |
| У5 Интерфейс и защита | | 63 ч |

Команда: @paoaoanaoal, @kholkink, @astya-t, @euiaow, @Alexeyokey.

## Как взять задачу

1. Открой очередь своей зоны в разделе [Очереди по зонам](#очереди-по-зонам). Задачи там стоят в том порядке, в котором их брать.
2. Возьми верхнюю открытую задачу, у которой закрыто всё из колонки «Ждёт». Если зависимость ещё открыта, но её контракт уже есть, начинай на заглушках, см. [Если зависимость ещё не готова](#если-зависимость-ещё-не-готова).
3. Назначь себя и поставь метку `в работе`: в issue справа **Assignees → assign yourself**, **Labels → в работе**. Или из терминала:
   ```bash
   gh issue edit 8 --add-assignee @me --add-label "в работе"
   ```
4. Создай ветку от `main` с ID задачи в имени: `D2-flights`, `R1-tripstate`.
5. Запусти `make check`, пока всё не пройдёт. Потом открой PR и в описании напиши `Closes #8`, шаблон PR подставится сам. На ревью позови того, кто стоит в колонке «Блокирует»: он будет пользоваться твоим кодом. Если там пусто, позови любого.
6. После мержа issue закроется сам. Бери следующую задачу.

Правила:

- В работе одна задача на человека. Вторую можно взять, только пока первая ждёт ревью.
- Застрял больше чем на 2 часа? Напиши в issue, что мешает, отметь того, кого ждёшь, и бери следующую задачу из очереди.
- Не успеваешь к концу дня из метки `Д`? Предупреди команду сразу, а не в последний день.

## Порядок

1. **По дням.** Сначала всё с меткой `Д1`, потом `Д2` и так далее. Метка `Д` означает срок, а не день старта: задачу `Д3` можно начать в Д2, если предыдущие уже закрыты.
2. **Внутри дня по приоритету:** `P0`, потом `P1`, потом `P2`. При равном приоритете первой бери задачу, которую кто-то ждёт, то есть с непустой колонкой «Блокирует».
3. **`P2` только когда закрыты все `P0` своей зоны.** Если к Д6 не успеваем, `P2` режем первыми, потом `P1`.
4. **Д1 начинается со встречи [#3](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/3) O1.** На ней фиксируем контракты и распределяем зоны. Пока контрактов нет, все работают вслепую.

Четыре задачи держат половину команды. Закрыть их нужно в первую очередь, к концу Д2:

| Задача | Кто её ждёт |
|---|---|
| [#4](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/4) **R1** Схема TripState | A2, A4, R2, R4, S1, U2 |
| [#2](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/2) **D1** Контракты инструментов и заглушки | A2, D2, D3, D6, R2 |
| [#1](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/1) **A1** → [#6](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/6) **U1** Репозиторий, затем каркас интерфейса | S2, U2, U3, U4, U7, U8 |
| [#5](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/5) **A2** Граф этапов и роутер | A3, A6, A7, A8, R5, U8 |

### Путь к демо

Самая длинная цепочка из задач `P0` занимает 41 ч работы подряд. Любая задержка в ней сдвигает демо:

[#2](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/2) D1 (4 ч, Д1) → [#8](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/8) D2 (10 ч, Д3) → [#15](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/15) S3 (4 ч, Д3) → [#24](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/24) S5 (13 ч, Д4) → [#35](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/35) D9 (4 ч, Д6) → [#36](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/36) U9 (6 ч, Д6)

Вторая цепочка ненамного короче, 39 ч: [#4](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/4) R1 → [#12](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/12) R2 → [#23](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/23) R4 → [#35](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/35) D9 → [#36](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/36) U9.

### Если зависимость ещё не готова

Не жди, работай от контрактов из O1 и заглушек из D1, а интегрируйся после мержа зависимости. Это особенно важно, когда зависимость из чужой зоны сдаётся в тот же день:

| Задача | Ждёт в тот же день | Что делать |
|---|---|---|
| [#15](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/15) S3, [#16](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/16) S4 (Д3) | [#8](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/8) D2, [#9](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/9) D3 (Д3) | D2 отдаёт три направления уже к Д2. D3 выкладывает первые отели как можно раньше |
| [#18](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/18) U3 (Д3) | [#12](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/12) R2 (Д3) | U3 верстается на трёх фиктивных вариантах по схеме R1 |
| [#32](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/32) R6 (Д5) | [#29](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/29) A8 (Д5) | Метрики сначала считаются по ручным прогонам, симулятор подключается потом |
| [#36](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/36) U9 (Д6) | [#35](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/35) D9 (Д6) | Слайды про архитектуру и данные делаются раньше, демо-часть в конце |
| [#40](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/40) U6 (Д6) | [#39](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/39) S7 (Д6) | Сначала экраны памяти по S6, сервис после покупки уже после S7 |

То же внутри зоны У4: простая версия [#13](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/13) S1 нужна уже к Д2, чтобы S2 было на чём собирать.

## По дням

| День | Задачи |
|---|---|
| Д1 | [#1](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/1) A1, [#2](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/2) D1, [#3](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/3) O1, [#4](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/4) R1 |
| Д2 | [#5](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/5) A2, [#6](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/6) U1 |
| Д3 | [#7](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/7) A3, [#8](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/8) D2, [#9](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/9) D3, [#10](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/10) D4, [#11](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/11) O2, [#12](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/12) R2, [#13](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/13) S1, [#14](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/14) S2, [#15](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/15) S3, [#16](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/16) S4, [#17](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/17) U2, [#18](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/18) U3, [#19](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/19) A6 |
| Д4 | [#20](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/20) A4, [#21](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/21) D5, [#22](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/22) R3, [#23](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/23) R4, [#24](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/24) S5 |
| Д5 | [#25](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/25) A5, [#26](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/26) R5, [#27](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/27) U4, [#28](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/28) U5, [#29](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/29) A8, [#30](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/30) D6, [#31](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/31) D7, [#32](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/32) R6, [#33](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/33) S6 |
| Д6 | [#34](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/34) A9, [#35](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/35) D9, [#36](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/36) U9, [#37](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/37) A7, [#38](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/38) D8, [#39](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/39) S7, [#40](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/40) U6, [#41](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/41) U7, [#42](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/42) A10, [#43](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/43) R7, [#44](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/44) R8, [#45](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/45) U8 |
| Д7 | [#46](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/46) O3, [#47](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/47) S8 |

## Общие задачи

Делаются всей командой. Каждый отмечает свою зону в чек-листе issue, последний закрывает issue.

| # | Задача | День | Оценка | Готово, когда |
|---|---|---|---|---|
| [#3](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/3) | **O1** Встреча по контрактам: TripState, интерфейсы инструментов, события для интерфейса | Д1 | 2 ч на каждого | Контракты лежат в репо, все работают от них |
| [#11](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/11) | **O2** 10 тестовых запросов по своей зоне с проверяемыми свойствами | Д3 | 2 ч на каждого | Запросы добавлены в общий набор эвала |
| [#46](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/46) | **O3** Заморозка, репетиция демо, фиксы | Д7 | 4 ч на каждого | Два чистых прогона демо подряд |

## Очереди по зонам

Порядок в таблице — порядок взятия. «Ждёт» — что должно быть закрыто до старта. «Блокирует» — кто ждёт эту задачу.

### У1 Агент и диалог — 63 ч

[Открытые задачи зоны](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22%D0%A31%20%D0%90%D0%B3%D0%B5%D0%BD%D1%82%20%D0%B8%20%D0%B4%D0%B8%D0%B0%D0%BB%D0%BE%D0%B3%22)

| # | Задача | День | P | Ждёт | Блокирует |
|---|---|---|---|---|---|
| [#1](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/1) | **A1** Репозиторий, docker-compose, CI и LLM-клиент | Д1 | P0 | — | [#6](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/6) U1 |
| [#5](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/5) | **A2** Граф этапов и роутер: цикл инструментов, события прогресса, простой разбор запроса | Д2 | P0 | [#2](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/2) D1, [#4](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/4) R1 | [#7](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/7) A3, [#19](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/19) A6, [#26](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/26) R5, [#29](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/29) A8, [#37](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/37) A7, [#45](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/45) U8 |
| [#7](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/7) | **A3** Мини-эвал 2–3 моделей на 30 запросах, выбор основной и быстрой | Д3 | P0 | [#5](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/5) A2 | — |
| [#19](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/19) | **A6** Трассировка вызовов (Langfuse или аналог) | Д3 | P1 | [#5](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/5) A2 | — |
| [#20](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/20) | **A4** Разбор сообщения в патч брифа и словарь скрытых требований | Д4 | P0 | [#4](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/4) R1 | [#25](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/25) A5, [#33](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/33) S6 |
| [#25](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/25) | **A5** Политика уточнений и допущений, кнопки быстрых ответов | Д5 | P0 | [#20](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/20) A4 | — |
| [#29](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/29) | **A8** Симулятор пользователя для эвала | Д5 | P1 | [#5](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/5) A2 | [#32](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/32) R6, [#42](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/42) A10 |
| [#34](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/34) | **A9** README: архитектура, запуск одной командой, источники данных, результаты эвала | Д6 | P0 | [#32](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/32) R6 | — |
| [#37](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/37) | **A7** Ограничители: запросы не по теме, инъекции, невозможные запросы | Д6 | P1 | [#5](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/5) A2 | — |
| [#42](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/42) | **A10** Смоук-эвал в CI на каждый PR | Д6 | P2 | [#29](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/29) A8 | — |

### У2 Данные и поиск — 63 ч

[Открытые задачи зоны](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22%D0%A32%20%D0%94%D0%B0%D0%BD%D0%BD%D1%8B%D0%B5%20%D0%B8%20%D0%BF%D0%BE%D0%B8%D1%81%D0%BA%22)

| # | Задача | День | P | Ждёт | Блокирует |
|---|---|---|---|---|---|
| [#2](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/2) | **D1** Контракты инструментов и заглушки с фиктивными ответами | Д1 | P0 | — | [#5](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/5) A2, [#8](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/8) D2, [#9](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/9) D3, [#12](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/12) R2, [#30](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/30) D6 |
| [#8](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/8) | **D2** Перелёты: Aviasales Data API и синтетика по реальной маршрутной сети | Д3 | P0 | [#2](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/2) D1 | [#10](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/10) D4, [#15](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/15) S3, [#38](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/38) D8 |
| [#9](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/9) | **D3** Отели: OSM и LiteAPI плюс синтетика цен, наличия и правил, заложенные ловушки | Д3 | P0 | [#2](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/2) D1 | [#10](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/10) D4, [#15](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/15) S3, [#16](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/16) S4, [#31](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/31) D7 |
| [#10](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/10) | **D4** Кэш внешних ответов (record/replay) и курсы ЦБ | Д3 | P0 | [#8](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/8) D2, [#9](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/9) D3 | — |
| [#21](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/21) | **D5** Индекс 40–80 направлений: климат, время полёта, визы, цены, риски | Д4 | P0 | — | [#27](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/27) U4 |
| [#31](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/31) | **D7** Профили отелей из отзывов: аспекты по сегментам гостей | Д5 | P1 | [#9](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/9) D3 | [#44](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/44) R8 |
| [#30](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/30) | **D6** ЖД: Яндекс Расписания и модель цен по классам вагонов | Д5 | P1 | [#2](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/2) D1 | — |
| [#35](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/35) | **D9** Демо-сценарий на заложенных ловушках и его прогоны | Д6 | P0 | [#23](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/23) R4, [#24](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/24) S5 | [#36](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/36) U9, [#47](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/47) S8 |
| [#38](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/38) | **D8** Календарь цен и подсказки по сдвигу дат | Д6 | P1 | [#8](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/8) D2 | — |

### У3 Ранжирование и пересборка — 63 ч

[Открытые задачи зоны](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22%D0%A33%20%D0%A0%D0%B0%D0%BD%D0%B6%D0%B8%D1%80%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D0%B5%20%D0%B8%20%D0%BF%D0%B5%D1%80%D0%B5%D1%81%D0%B1%D0%BE%D1%80%D0%BA%D0%B0%22)

| # | Задача | День | P | Ждёт | Блокирует |
|---|---|---|---|---|---|
| [#4](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/4) | **R1** Схема TripState: источники полей, версии, дифф | Д1 | P0 | — | [#5](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/5) A2, [#12](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/12) R2, [#13](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/13) S1, [#17](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/17) U2, [#20](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/20) A4, [#23](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/23) R4 |
| [#12](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/12) | **R2** Ранжирование: фильтры, скоринг по весам, полная стоимость, Парето, 3 архетипа | Д3 | P0 | [#2](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/2) D1, [#4](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/4) R1 | [#18](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/18) U3, [#22](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/22) R3, [#23](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/23) R4, [#44](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/44) R8 |
| [#22](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/22) | **R3** Факты для объяснений: почему подходит, чем пожертвовали, риски | Д4 | P0 | [#12](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/12) R2 | [#26](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/26) R5 |
| [#23](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/23) | **R4** Пересборка: граф зависимостей, минимальные изменения, дифф и откат | Д4 | P0 | [#4](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/4) R1, [#12](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/12) R2 | [#28](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/28) U5, [#35](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/35) D9, [#39](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/39) S7 |
| [#26](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/26) | **R5** Текст объяснений по фактам и проверка чисел | Д5 | P0 | [#5](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/5) A2, [#22](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/22) R3 | [#43](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/43) R7 |
| [#32](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/32) | **R6** Метрики эвала и страница-дашборд | Д5 | P1 | [#29](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/29) A8 | [#34](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/34) A9, [#36](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/36) U9 |
| [#43](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/43) | **R7** LLM-судья качества объяснений | Д6 | P2 | [#26](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/26) R5 | — |
| [#44](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/44) | **R8** Кабинет отеля: кому и почему его рекомендуют | Д6 | P2 | [#12](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/12) R2, [#31](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/31) D7 | — |

### У4 Сборка и покупка — 63 ч

[Открытые задачи зоны](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22%D0%A34%20%D0%A1%D0%B1%D0%BE%D1%80%D0%BA%D0%B0%20%D0%B8%20%D0%BF%D0%BE%D0%BA%D1%83%D0%BF%D0%BA%D0%B0%22)

| # | Задача | День | P | Ждёт | Блокирует |
|---|---|---|---|---|---|
| [#13](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/13) | **S1** Корзина, итог, имитация оплаты, подтверждение, ваучер и .ics | Д3 | P0 | [#4](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/4) R1 | [#14](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/14) S2, [#39](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/39) S7 |
| [#15](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/15) | **S3** Таймлайн поездки в местном времени с часовыми поясами | Д3 | P0 | [#8](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/8) D2, [#9](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/9) D3 | [#24](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/24) S5 |
| [#16](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/16) | **S4** Трансферы: время в пути через OSRM и тарифная модель | Д3 | P0 | [#9](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/9) D3 | [#24](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/24) S5 |
| [#14](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/14) | **S2** Экраны корзины, формы путешественников и подтверждения | Д3 | P0 | [#6](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/6) U1, [#13](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/13) S1 | — |
| [#24](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/24) | **S5** Валидатор стыковок: правила и исправления с ценой | Д4 | P0 | [#15](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/15) S3, [#16](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/16) S4 | [#28](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/28) U5, [#35](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/35) D9 |
| [#33](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/33) | **S6** Память: профиль, предпочтения, «что я о вас помню» | Д5 | P1 | [#20](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/20) A4 | [#40](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/40) U6 |
| [#39](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/39) | **S7** После покупки: перенос дат, ранний заезд, трансфер, напоминания | Д6 | P1 | [#13](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/13) S1, [#23](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/23) R4 | [#40](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/40) U6 |
| [#47](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/47) | **S8** Видео полного прохода для защиты | Д7 | P0 | [#35](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/35) D9 | — |

### У5 Интерфейс и защита — 63 ч

[Открытые задачи зоны](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22%D0%A35%20%D0%98%D0%BD%D1%82%D0%B5%D1%80%D1%84%D0%B5%D0%B9%D1%81%20%D0%B8%20%D0%B7%D0%B0%D1%89%D0%B8%D1%82%D0%B0%22)

| # | Задача | День | P | Ждёт | Блокирует |
|---|---|---|---|---|---|
| [#6](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/6) | **U1** Каркас, дизайн-система, чат со стримингом и шагами агента | Д2 | P0 | [#1](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/1) A1 | [#14](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/14) S2, [#17](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/17) U2, [#18](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/18) U3, [#27](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/27) U4, [#41](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/41) U7, [#45](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/45) U8 |
| [#17](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/17) | **U2** Холст брифа: плашки с источниками, правка допущений, быстрые ответы | Д3 | P0 | [#4](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/4) R1, [#6](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/6) U1 | — |
| [#18](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/18) | **U3** Сравнение: таблица и карточки «почему, чем пожертвовали, риски», веса | Д3 | P0 | [#6](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/6) U1, [#12](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/12) R2 | — |
| [#27](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/27) | **U4** Карточки направлений и карта | Д5 | P0 | [#6](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/6) U1, [#21](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/21) D5 | — |
| [#28](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/28) | **U5** Таймлайн с флагами валидатора, исправлениями и карточкой «было → стало» | Д5 | P0 | [#23](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/23) R4, [#24](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/24) S5 | — |
| [#36](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/36) | **U9** Слайды для защиты | Д6 | P0 | [#32](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/32) R6, [#35](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/35) D9 | — |
| [#40](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/40) | **U6** Экраны памяти и сервиса после покупки | Д6 | P1 | [#33](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/33) S6, [#39](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/39) S7 | — |
| [#41](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/41) | **U7** Мобильная адаптация | Д6 | P1 | [#6](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/6) U1 | — |
| [#45](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/45) | **U8** Telegram: бот и Mini App на том же фронте | Д6 | P2 | [#5](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/5) A2, [#6](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues/6) U1 | — |

## Полезные фильтры

- [Мои задачи](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues?q=is%3Aissue%20is%3Aopen%20assignee%3A%40me)
- [В работе](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22%D0%B2%20%D1%80%D0%B0%D0%B1%D0%BE%D1%82%D0%B5%22)
- [Открытые P0](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues?q=is%3Aissue%20is%3Aopen%20label%3AP0)
- [Никто не взял](https://github.com/ostrovok-hackathon-2026/na-maldivy/issues?q=is%3Aissue%20is%3Aopen%20no%3Aassignee)

**Кэш данных и валюты D4.** `CachedHttpClient` поддерживает `record`/`replay`/`live`,
а валютное ядро считает через `Decimal` с учётом номинала ЦБ. Сохранённый снимок
за 24.10.2026 работает без сети: `make d4-demo`; проверки — `make d4-check`.
Режим задаётся `CACHE_MODE`, для демо и проверок принудительно используется `replay`.
Инструкция и границы готовности — в [docs/data-cache.md](docs/data-cache.md).
