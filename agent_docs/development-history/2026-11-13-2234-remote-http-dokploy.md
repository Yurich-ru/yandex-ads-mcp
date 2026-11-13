# [2026-11-13 22:34] Удалённый HTTP-деплой: Dockerfile, mcp-proxy, Dokploy

Файл: `agent_docs/development-history/2026-11-13-2234-remote-http-dokploy.md`

## Что сделано

- Добавлены `Dockerfile` (финальный stage `production`, `mcp-proxy==0.15.0` + `mcp==1.30.0`,
  непривилегированный пользователь, healthcheck `/healthz`, CMD с `${PUBLIC_HOST:?}`)
  и `.dockerignore`. Образ собран локально на Docker Desktop.
- README: раздел «Удалённый доступ (Docker / Dokploy)» — сборка/запуск, эндпоинты,
  `PUBLIC_HOST`, Basic Auth, сниппеты клиентов, предупреждение про общий набор токенов.
- `.env.example`: закомментированный блок `PUBLIC_HOST` (читает обёртка, не `server.py`).
- `operations.md`: раздел «Удалённый HTTP-сервис (Dokploy)», прод-доступ, 4 новые грабли
  (пустой `--allowed-host`, `host:*` vs голый Host, Origin 403, stage `production`).
- ADR: выбор mcp-proxy и Basic Auth Dokploy вместо своего транспорта и Bearer-токена.
- CHANGELOG: пункт в Unreleased без bump версии — инфраструктура, код сервера не менялся.
- Локальный smoke на Docker Desktop: `/healthz` 204, initialize → session id,
  `tools/list` 152, health `healthy`, mutating-вызов без confirm → превью.
- Dokploy: приложение `yandex-ads-mcp` (`px5-RaAYK7Wy6pdnHmKOQ`) на `netcup-vps`,
  GitHub-провайдер (доступ есть), сборка dockerfile/`production`, env с плейсхолдерами
  секретов, домен `yandex.riddler.agency` (Let's Encrypt), `autoDeploy:false`.
  Секреты и Basic Auth владелец задал в UI. Деплой `done`.
- Живая проверка: 401 без credentials, 204 `/healthz` с auth, handshake через Traefik,
  `tools/list` 152, `yd_direct_accounts_get` вернул живой кабинет (токен рабочий),
  mutating без confirm → превью. `yd_yandex_accounts_get` не зарегистрирован:
  задан `YD_OAUTH_TOKEN`, а не `YANDEX_TOKENS` (инструмент появляется только с реестром).

## Зачем

Подключать сервер удалённо из Claude Code / Cursor / Codex, не переписывая stdio-сервер
и не подвергая боевые кабинеты риску (`YD_CONFIRM=true`, секреты только в env Dokploy).

## Обновлено

- [x] agent_docs/adr/2026-11-13-2234-http-bridge-dokploy.md
- [x] Тесты: `test_safety.py` офлайн — зелёный (код сервера не менялся)
- [x] Документация: README, .env.example, operations.md, snapshot.md, CHANGELOG.md

## Связанные решения

- agent_docs/adr/2026-11-13-2234-http-bridge-dokploy.md

## Следующие шаги

- Владелец подключает клиент (Claude Code / Cursor / `mcp-remote`) и подтверждает список инструментов.
- Ревью и мерж PR #1 в `codex/custom`, затем переключить ветку приложения и редеплой.
- Если нужен `yd_yandex_accounts_get`: задать `YANDEX_TOKENS` + `YANDEX_DEFAULT_LOGIN` в env и редеплой.
