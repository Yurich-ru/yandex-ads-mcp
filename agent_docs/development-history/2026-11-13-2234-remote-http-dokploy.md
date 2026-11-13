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

- Локальный smoke-тест контейнера (healthz, initialize, tools/list, healthy).
- Dokploy: DNS, приложение, env, домен, деплой; секреты и Basic Auth — через UI владельцем.
- После ревью PR: мерж в `codex/custom`, переключение ветки приложения, финальные ID в operations.md.
