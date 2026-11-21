# Навигация по документам

Короткая навигация. Читать только релевантные файлы.

## Контекст

- `agent_docs/glossary.md` — термины, аббревиатуры, кодовые названия проекта.
- Описание и цели проекта — в `AGENTS.md`, раздел «Описание проекта».

## Основные

- `agent_docs/architecture.md` — архитектура и компоненты; актуально при изменениях системы.
- `agent_docs/invariants.md` — жёсткие правила продукта (нарушение = баг); сверяться при изменениях данных, безопасности, API.
- `agent_docs/adr/` — атомарный журнал значимых решений (по файлу на решение). Правила: `agent_docs/adr/README.md`.
- `agent_docs/development-history/` — атомарный журнал итераций (по файлу на итерацию). Правила: `agent_docs/development-history/README.md`.
- `agent_docs/specs/` — ТЗ задач (по файлу на задачу): требования и критерии приёмки, согласованные до кода. Правила: `agent_docs/specs/README.md`; порядок работы — скилл `/spec`.

## Состояние (читать при старте сессии, обновлять при завершении)

- `agent_docs/snapshot.md` — текущая точка работы.
- `agent_docs/backlog.md` — план: Next / Soon / Later / Won't do.
- `agent_docs/backlog-closed.md` — закрытые пункты бэклога с доказательством; при старте не читать, искать по номеру.

## Правила и гайды

- `agent_docs/guides/dod.md` — критерии завершенности (DoD).
- `agent_docs/guides/autodeploy.md` — **стандарт автодеплоя** (push в main → CI-гейт → webhook Portainer → health-гейт): соглашения compose/стека, что настраивается автоматически (gh, Portainer API-токен), что руками. Читать при подключении деплоя и правке `docker-compose*.yml`.
- `agent_docs/guides/operations.md` — **операции**: запуск, тесты, деплой, секреты, доступ к проду, журнал инфраструктурных грабель. Читать перед запуском/деплоем/отладкой окружения; пополнять после каждой разгаданной инфра-проблемы.
- `agent_docs/guides/environment-setup.md` — настройка окружения; применять при инициализации проекта.
- `agent_docs/guides/context-management.md` — **управление контекстом сессии**: когда `/clear`,
  когда `/compact`, что записать перед сбросом. Читать, когда сессия разрослась или задача закрыта.
- `agent_docs/guides/logging.md` — логирование скриптов/интеграций.
- `agent_docs/guides/page-audits.md` — **замер публичных страниц** (скорость, SEO, доступность):
  PageSpeed Insights вместо локального Chrome, W3C Nu, браузер через MCP `playwright`.
  Читать перед любым замером страницы.
- `agent_docs/guides/atomic-documents.md` — правила атомарных событийных документов.
- `agent_docs/guides/archiving-and-temp.md` — архивация и временные файлы.

## Шаблоны

- `agent_docs/templates/architecture.md`
- `agent_docs/templates/adr.md`
- `agent_docs/templates/development-history.md`
- `agent_docs/templates/spec.md`

## Инициализация и обновление

- `agent_docs/setup-checklist.md` — чек-лист для нового проекта (удаляется после прохождения).
- `STARTER_VERSION`, `STARTER_MANIFEST` — версия шаблона и граница «шаблон / проект». Обновлять стартер в проекте — скилл `/starter-upgrade`.
