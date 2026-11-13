# [2026-11-13 22:34] Удалённый HTTP-доступ через mcp-proxy и Dokploy

Файл: `agent_docs/adr/2026-11-13-2234-http-bridge-dokploy.md`

## Статус

Принято

## Контекст

Сервер умеет только stdio (`server.py`), а подключаться нужно удалённо из Claude Code /
Cursor / Codex. Нужны TLS, аутентификация и хостинг без переписывания сервера. На
`netcup-vps` уже работает тот же паттерн для `telegram-mcp`: GitHub → сборка Dockerfile →
домен Let's Encrypt → Basic Auth в Dokploy.

## Решение

- Не добавлять HTTP-транспорт в `server.py`. Образ оборачивает stdio-процесс через
  `mcp-proxy==0.15.0` (streamable HTTP `/mcp`, legacy `/sse`, `/healthz`).
- Хостинг — Dokploy на `netcup-vps`, домен `yandex.riddler.agency`, порт 8765,
  финальный stage `production`.
- Аутентификация — Basic Auth средствами Dokploy (Traefik), не `MCP_PROXY_AUTH_TOKEN`.
- Защита кабинета — `YD_CONFIRM=true` в env приложения; секреты только в env-блоке Dokploy.
- Ветка деплоя `feat/remote-deploy` от `codex/custom` (включает общий список аккаунтов),
  после ревью — `codex/custom`.

## Обоснование

- mcp-proxy — готовый сопровождаемый мост: не нужно писать и поддерживать свой
  HTTP/SSE-слой, сессии и healthcheck. Версия запинена; `mcp==1.30.0` пинен явно, потому
  что mcp-proxy требует `mcp>=1.30,<2`, а `requirements.txt` допускает `>=1.0`.
- Basic Auth в Dokploy даёт управление пользователями из UI и один заголовок
  `Authorization` на всех клиентов. `MCP_PROXY_AUTH_TOKEN` занял бы тот же заголовок —
  варианты взаимоисключающие, выбран UI-путь как у telegram-mcp.
- Один дочерний stdio-процесс = один набор токенов: сервер так и устроен (один процесс —
  один env). Несколько кабинетов покрываются `YANDEX_TOKENS` / `YD_DIRECT_TOKENS`,
  а не репликами.

## Последствия

- Положительные: код сервера не тронут; релизный процесс пакета не меняется; TLS, auth и
  редеплой — штатные механизмы Dokploy; откат = revert ветки/env и редеплой.
- Отрицательные: сессии stateful и обрываются при каждом редеплое (клиент
  переинициализируется; запасной путь — `--stateless`); вторая реплика невозможна;
  Basic Auth — общий секрет на клиента, не изоляция арендаторов (`YD_ALLOWED_LOGINS` тоже
  не граница арендаторов). Нельзя добавлять Traefik `compress`/`forwardAuth` middleware к потокам.

## Связанные записи

- `agent_docs/development-history/2026-11-13-2234-remote-http-dokploy.md`
- `agent_docs/guides/operations.md` — раздел «Удалённый HTTP-сервис (Dokploy)»

## Альтернативы рассмотрены

- Свой HTTP/SSE-транспорт в `server.py` — отклонено: код и поддержка транспортого слоя,
  сессий и аутентификации ради того, что уже есть в mcp-proxy.
- `MCP_PROXY_AUTH_TOKEN` (Bearer) — отклонено: конфликт с Basic Auth за заголовок
  `Authorization`, нет UI-управления пользователями.
- Отдельный reverse proxy с ручной настройкой TLS — отклонено: Dokploy уже даёт домен,
  Let's Encrypt и Basic Auth из коробки.
