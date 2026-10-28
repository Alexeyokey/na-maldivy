# Публичное демо на VPS

Конфигурация `deploy/vps/compose.yml` запускает Nginx → web → agent. Снаружи открыт только HTTP-порт 80; web и agent находятся во внутренней Docker-сети. Поиск использует сохранённые котировки D4 и явно подписанные моки, поезда синтетические, языковая модель и оплата имитируются. API-ключи не нужны и в контейнеры не передаются. В форме оформления используйте только вымышленные имена и тестовые даты рождения.

28.10.2026 выпуск `faf779edab3e9237ed626b51526d2589ba453dd7` фактически
развёрнут на [публичном сервере](http://201.8.90.64). Проверены три направления
через внешний HTTP, полный браузерный путь Антальи, скачивания, сохранение
заказов после перезапуска, автозапуск и закрытость внутренних портов.
[Отчёт и снимки](../eval/reports/vps-deployment-2026-10-28/report.json).

Сборка подходит для Ubuntu 24.04 x86_64 с Docker Engine и Compose v2. Образы собираются на сервере; архив готового локального демо для Apple Silicon здесь не используется. Лимиты памяти контейнеров: agent 384 МиБ, web 128 МиБ, Nginx 64 МиБ. На VPS с 1 ГБ собирайте образы последовательно.

## Размещение исходников и запуск

Публичные исходники конкретного коммита размещаются в `/srv/na-maldivy/releases/COMMIT`, где `COMMIT` — идентификатор версии. В эту папку входят `backend/app`, Python lock-файл и описание зависимостей, `data`, `frontend`, Dockerfile и `deploy/vps`. Не копируйте `.env`, `.git`, локальные окружения, runtime-базы, приватные отчёты или ключи. В файле `RELEASE_COMMIT` рядом с исходниками сохраните идентификатор версии. `/srv/na-maldivy/current` — символическая ссылка на выбранную папку выпуска.

Из выбранной папки выпуска:

```sh
release=/srv/na-maldivy/current
export NA_MALDIVY_RELEASE="$(cat "$release/RELEASE_COMMIT")"
docker compose --env-file /dev/null -p na-maldivy-public -f "$release/deploy/vps/compose.yml" config --quiet
docker compose --env-file /dev/null -p na-maldivy-public -f "$release/deploy/vps/compose.yml" build agent
docker compose --env-file /dev/null -p na-maldivy-public -f "$release/deploy/vps/compose.yml" build web
docker compose --env-file /dev/null -p na-maldivy-public -f "$release/deploy/vps/compose.yml" up -d --wait --wait-timeout 120
```

Имена образов содержат `NA_MALDIVY_RELEASE`, чтобы прежняя сборка оставалась доступна для отката. `--env-file /dev/null` исключает чтение случайного `.env`; конфигурация содержит все необходимые публичные настройки. Один процесс backend обслуживает временные чат-сессии. Постоянный том `na-maldivy-public_agent-state` содержит SQLite-заказы и не зависит от папки выпуска.

## Проверка

Внешний `/health` должен вернуть `{"status":"ok","llm_provider":"fake"}`. Главная страница должна открыться, а `/config.js` — показать режим `api`. Порты 5173, 8000 и 8080 не публикуются. Служебный backend `/docs` через web не доступен.

С машины с исходниками и Python 3 выполните полный HTTP-прогон; подставьте адрес VPS вместо `SERVER_IP`:

```sh
python3 scripts/check_running_three_destinations.py \
  --base-url http://SERVER_IP \
  --output /tmp/vps-demo-check.json \
  --context /tmp/vps-demo-private.json
```

Прогон проверяет три направления, источники рейсов, D7/D8, корзину, отказ и успешную имитацию оплаты, ваучер и календарь. Приватный файл контекста содержит доступ к тестовым заказам: не добавляйте его в Git и не публикуйте.

Затем на VPS перезапустите agent той же командой Compose с `restart agent`, дождитесь успешного `/health` и повторите проверку с `--resume`. Временные чат-сессии должны исчезнуть, а оплаченные демо-заказы и документы — сохраниться. Дополнительно через браузер пройдите путь Анталья → выбор → подтверждение → вымышленные путешественники → две отметки → отказ оплаты → успешная демо-оплата → скачивание документов. Для Дубая проверьте маркировку «ДЕМО-МОК».

Access log выключен у Nginx и Uvicorn, чтобы не сохранять приватные идентификаторы из URL. Docker-логи ограничены тремя файлами по 10 МиБ на контейнер. Nginx ограничивает тело запроса 64 КиБ, API — 10 запросами/с с кратким запасом 80, чат — 2 запросами/с с запасом 20, соединения — восемью на адрес. SSE передаётся сразу, ответы не кэшируются.

## Резервная копия состояния

Перед обновлением остановите agent, чтобы получить согласованную копию SQLite, и скопируйте `/state` из остановленного контейнера. Каталог резервной копии должен быть доступен только администратору:

```sh
backup="/srv/na-maldivy/backups/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$backup"
docker compose --env-file /dev/null -p na-maldivy-public -f "$release/deploy/vps/compose.yml" stop agent
agent_container="$(docker compose --env-file /dev/null -p na-maldivy-public -f "$release/deploy/vps/compose.yml" ps -a -q agent)"
docker cp "$agent_container:/state/." "$backup/"
chmod -R go-rwx "$backup"
docker compose --env-file /dev/null -p na-maldivy-public -f "$release/deploy/vps/compose.yml" start agent
```

Если копирование завершилось ошибкой, всё равно запустите agent обратно. Резервная копия содержит сведения тестовых путешественников и заказы; храните её вне публичных исходников.

## Обновление и откат

Подготовьте новую папку выпуска, соберите agent и web последовательно с её `NA_MALDIVY_RELEASE`, затем переключите `current` на новую папку и выполните `up -d --force-recreate --wait`. Это пересоздаёт Nginx вместе с web, чтобы он заново разрешил внутренний адрес web. Сохраняйте один и тот же проект `na-maldivy-public`: он подключает прежний том состояния. После обновления повторите `/health` и три направления.

Для отката переключите `current` на предыдущий выпуск, заново прочитайте его `RELEASE_COMMIT` в `NA_MALDIVY_RELEASE` и выполните `up -d --no-build --force-recreate --wait`. Образы предыдущего выпуска должны оставаться на сервере. Состояние сохраняется; при несовместимом изменении схемы базы сначала остановите agent и восстановите соответствующую приватную резервную копию. Не используйте `down -v` и не удаляйте `na-maldivy-public_agent-state` при обновлении или откате.
