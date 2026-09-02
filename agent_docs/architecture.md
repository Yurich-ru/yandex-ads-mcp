# Архитектура

## Обзор

Один Python-процесс, говорящий с AI-ассистентом по stdio-протоколу MCP и с четырьмя API
Яндекса по HTTPS. Состояния между вызовами нет, кроме кэша IAM-токена для Wordstat.
151 инструмент: Директ 85, Метрика 43, Аудитории 23, Wordstat 5.

## Контекст

- **Клиент:** любой MCP-совместимый ассистент (Claude Code, Cursor, Windsurf) — запускает
  процесс сам, передаёт токены через `env` в конфиге `mcpServers`.
- **Внешние API:** Яндекс Директ v5 (`api.direct.yandex.com`, sandbox отдельным хостом),
  Метрика (management + reporting), Аудитории (`api-audience.yandex.ru/v1`), Wordstat через
  Yandex Cloud Search API (`searchapi.api.cloud.yandex.net`) с IAM-токеном или Api-Key.

## Ключевые компоненты

- `server.py` — конфиг из env (`_env_bool`, карта токенов `YD_DIRECT_TOKENS`), защитная обвязка
  (read-only, confirm, `YD_ALLOWED_LOGINS`, `client_login` через `contextvars`, retry/backoff,
  учёт очков `Units`), инструменты Директа и Wordstat, регистрация модулей, точка входа `main_sync`.
- `tools_direct_extra.py` — дополнительные инструменты Директа (ключевые фразы, ретаргетинг,
  аудитории и пр.) и `annotate_partial()` — разбор per-item ошибок в `_partial_success`.
- `tools_metrika.py` — инструменты Метрики (`METRIKA_TOOLS`, `register_metrika_handlers`).
- `tools_audience.py` — инструменты Аудиторий (`AUDIENCE_TOOLS`, `register_audience_handlers`).
- `test_safety.py` — офлайн-проверка классификации инструментов и защитных режимов.
- Project-local skills source: `.agents/skills/<name>/SKILL.md`.
- Platform skill mirrors: `.claude/skills/`, `.codex/skills/`, `.cursor/skills/`.

## Потоки данных

1. Ассистент вызывает инструмент → `server.py` проверяет режимы (`YD_READONLY` блокирует
   мутацию до сети; `YD_CONFIRM` без `confirm=true` возвращает превью).
2. Выбирается кабинет: логин из `YD_DIRECT_TOKENS` → его токен без `Client-Login`; иначе
   основной токен + заголовок `Client-Login` (агентский путь), с проверкой `YD_ALLOWED_LOGINS`.
3. HTTP-вызов с retry на 429/5xx; ответ Директа проходит через `annotate_partial()`.
4. Результат возвращается ассистенту как текст JSON; тела запросов логируются только при
   `YD_LOG_BODIES=true`.

## Технологии и зависимости

Python ≥ 3.10, `mcp>=1.0,<2`, `httpx`. Сборка — setuptools, консольный скрипт `yandex-ads-mcp`.
CI — GitHub Actions: `test.yml` (push/PR в `master`), `markdownlint.yml`.

## Нефункциональные требования и ограничения

- Защитные механизмы строго opt-in — дефолтное поведение как у базового MCP-сервера.
- Токены не попадают в логи; файл лога — только по явному `YD_LOG_FILE`.
- Очки Direct API ограничены — балансы из заголовка `Units` логируются, WARN при < 5 %.
- OAuth-токены, выпущенные после 2026-06-01, Yandex Cloud не меняет на IAM → для Wordstat
  нужен `YC_API_KEY`.

## Roadmap

См. `agent_docs/backlog.md`.
