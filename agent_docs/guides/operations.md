# Операции: запуск, деплой, прод

**Назначение:** всё, что нельзя вывести из кода — как запустить, как задеплоить, где живут секреты,
как посмотреть прод и на какие инфраструктурные грабли уже наступили. Третья ось памяти проекта
(рядом с контрактами и state, см. `AGENTS.md` → «Память проекта»).

**Когда заполнять:** при первом запуске проекта локально, при первом деплое и **каждый раз, когда
разгадывание инфраструктуры заняло больше пары минут** — иначе следующая сессия разгадает это заново.

---

## Запуск локально

- Как поднять: сервер stdio, его запускает ассистент по конфигу `mcpServers` (README «Быстрый старт»).
  Проверка импорта руками: `YD_OAUTH_TOKEN=x venv/bin/python -c "import server"`.
- Зависимости/окружение: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`;
  в репозитории venv уже есть (`venv/`, gitignored). Интерпретатор — `python3` (≥ 3.10).
- Тесты: `venv/bin/python test_safety.py` — офлайн, без токенов и сети.
- Windows: `python -m venv .venv`, затем `.venv/Scripts/python.exe -m pip install -r requirements.txt`
  и `.venv/Scripts/python.exe test_safety.py`. Если встроенный `ensurepip` не сработал, зависимости
  можно установить командой `uv pip install --python .venv/Scripts/python.exe -r requirements.txt`.
  В песочнице Codex от 2026-11-06 установка потребовала доступа к PyPI, а даже
  `asyncio.run(asyncio.sleep(0))` зависал. Офлайн-прогон вне песочницы прошёл без обращения к API.

## Деплой

Пакет запускается локально ассистентом (stdio) и дополнительно развёрнут как удалённый
HTTP-сервис на Dokploy. Portainer-стека нет.

### Релиз пакета (локальный stdio)

- Триггер: коммит в `master` с версией в скобках в сообщении.
- Пайплайн: GitHub Actions `test.yml` (push/PR в `master`) гоняет `test_safety.py`; `markdownlint.yml` — линт Markdown.
- Что обновить при релизе: `pyproject.toml` (version), `CHANGELOG.md` (запись с датой), счётчик инструментов в README и docstring `server.py`.
- Как проверить, что доехало: зелёный workflow на `master`; у пользователей — `git pull` и перезапуск ассистента.
- Как откатить: обычный `git revert`, пользователи подтягивают.

### Удалённый HTTP-сервис (Dokploy)

- Хост: `netcup-vps` (Debian 13), serverId `hyp_1VMpWnHITJRldL4QO`; проект `netcup-vps`,
  окружение `production` (`y1TvpSgCQqqzMVYxzPpjS`).
- Приложение `yandex-ads-mcp` (applicationId `px5-RaAYK7Wy6pdnHmKOQ`, appName
  `yandex-ads-mcp-sbtsma`); домен `yandex.riddler.agency` (A → 62.83.19.26,
  Let's Encrypt, domainId `07YJHhPDevkTT4YcMvWe4`), порт 8765.
- Источник: GitHub `rypMaH/yandex-ads-mcp`, ветка `codex/custom` (PR #1 смержен),
  сборка Dockerfile, финальный stage `production`. Автодеплой выключен.
- Мост stdio→HTTP: `mcp-proxy==0.15.0` (`/mcp`, legacy `/sse`, `/healthz` → 204);
  один дочерний процесс = один набор токенов, вторая реплика невозможна.
- Несекретный env (задан через MCP): `PUBLIC_HOST=yandex.riddler.agency`,
  `YD_CONFIRM=true`, `YD_LOG_LEVEL=INFO`.
- Секреты: только env-блок приложения в Dokploy UI (`YD_OAUTH_TOKEN` или
  `YANDEX_TOKENS` + `YANDEX_DEFAULT_LOGIN`, опционально `YC_FOLDER_ID`/`YC_API_KEY`,
  per-service токены). В чат, репозиторий и этот файл не попадают.
- Редеплой: `application-deploy` (Dokploy MCP) или Deploy в UI; статус — `application-one`,
  логи — `application-readLogs` / UI. Сессии stateful: каждый редеплой их обрывает,
  клиент должен переинициализироваться.
- Basic Auth: пользователи задаются в UI (Advanced → Security); после изменения —
  `application-reload` без пересборки. Basic Auth и `MCP_PROXY_AUTH_TOKEN` несовместимы
  (оба используют заголовок `Authorization`).

## Секреты и переменные окружения

- Где хранятся: локально `.env` (gitignored); у пользователей — блок `env` в конфиге ассистента. Полный список — README «Переменные окружения».
- Что нужно, чтобы добавить новую переменную: чтение в `server.py` через `os.environ`/`_env_bool`, строка в таблице README, строка в `.env.example`.
- Что НЕ задавать вручную: IAM-токен для Wordstat — обменивается автоматически из OAuth или `YC_API_KEY`, TTL из `expiresAt`.

Секреты в этот файл не копировать (см. INV-001) — только имена и место хранения.

## Доступ к прод-окружению

Прод — удалённый HTTP-эндпоинт `https://yandex.riddler.agency/mcp` на `netcup-vps`
(Dokploy, Basic Auth). Боевые кабинеты клиентов — это внешние API; при работе с ними включать
`YD_READONLY=true` или `YD_CONFIRM=true` и ограничивать `YD_ALLOWED_LOGINS`.

**Запись в боевой кабинет — необратимое действие: согласовывать с владельцем кабинета.**
Проверять логику без следов — на `YD_SANDBOX=true`.

## Журнал инфраструктурных грабель

Каждая запись — симптом, причина, правило на будущее. Ценность в том, что симптом узнаваем:
следующая сессия ищет по симптому, а не переоткрывает причину.

| Симптом (что видно) | Причина | Правило на будущее |
|---|---|---|
| Windows: офлайн-тест зависает при первом `asyncio.run()` в sandbox | Цикл событий Windows создаёт локальную пару сокетов. В трассировке зависание видно в `socket._fallback_socketpair` | Запустить `.venv/Scripts/python.exe -B test_safety.py` с разрешённым loopback. Тесты используют заглушки и не обращаются к API Яндекса |
| Wordstat отвечает ошибкой авторизации при валидном OAuth-токене | OAuth-токены, выпущенные после 2026-06-01, Yandex Cloud больше не меняет на IAM | Задавать `YC_API_KEY` сервисного аккаунта с ролью `search-api.executor` |
| `yd_audience_*` возвращают `403 access_denied` | В OAuth-приложении нет права «Яндекс Аудитории» | Добавить право на oauth.yandex.ru и **перевыпустить** токен |
| Ретаргетинг на сегмент Аудиторий — ошибка 8800 «Объект не найден» | Директ ждёт `ExternalId = id + 2 000 000 000`, а не сырой id | Передавать `audience_segment_id`, кодировку добавляет инструмент |
| Второй кабинет Директа не подключается через `Client-Login` | Кабинет самостоятельный (например субклиент eLama), агентского токена нет | `YD_DIRECT_TOKENS=логин:токен`, выбирать через `client_login` |
| CI на PR внешнего контрибьютора «no checks reported», прогоны в статусе `action_required` | GitHub не запускает workflow первого контрибьютора без ручного одобрения | Одобрить: `gh api -X POST repos/<owner>/<repo>/actions/runs/<id>/approve` (id — из `gh api ".../actions/runs?event=pull_request"`) |
| `gh run list --branch` → `unknown flag` | В dev-контейнере старый `gh` 2.4.0 (2022) | Статусы CI смотреть через `gh api "repos/<owner>/<repo>/actions/runs?branch=master"` |
| Облачная рутина (claude.ai routines): curl к api.github.com отвечает «GitHub access to this repository is not enabled for this session» | Облачная сессия проксирует GitHub API и открывает его только для репозиториев, подключённых к сессии; `add_repo` даёт лишь git-чтение | Для чужих публичных репозиториев из облака: данные веток — через `git ls-remote`, комментарии/issues чужих репо из облака недоступны — проверять локально |
| Контейнер yandex-ads-mcp сразу завершается: `PUBLIC_HOST must be set` | CMD подставляет `${PUBLIC_HOST:?}` в `--allowed-host`; пустое значение дало бы невалидный authority и падение позже | Задавать `PUBLIC_HOST` в env приложения до первого деплоя; без секретов контейнер всё равно рестарт-лупит — env заполнять целиком |
| Клиент получает `421 Invalid Host header` при верном домене | SDK сверяет Host точно либо по шаблону `host:*`; Traefik на 443 шлёт голый Host без порта | В `--allowed-host` передавать оба варианта: `host` и `host:*` |
| Inspector/браузер получает `403 Invalid Origin header` на `/mcp` | Запрос с заголовком `Origin`, CORS по умолчанию запрещён | `--allow-origin=https://<host>` в CMD обёртки |
| Dokploy собирает образ не из того stage | Финальный stage Dockerfile не совпадает с `dockerBuildStage` | Финальный stage называть `production` (`FROM … AS production`) и указывать его в `application-saveBuildType` |
