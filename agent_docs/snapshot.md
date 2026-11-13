# Snapshot — текущая точка работы

Обновляется при завершении каждой сессии/задачи. Отвечает на вопрос «где мы сейчас», а не «что планируем» (планы — в `backlog.md`).

> При обновлении **чистить устаревшее, а не только дописывать**: сделанное убирать из «Известные
> проблемы»/«Следующие шаги». Snapshot, накопивший историю, перестаёт отвечать на свой вопрос —
> история живёт в `development-history/`.

**Статус:** стабильный релиз 1.3.0; удалённый HTTP-деплой (Dokploy) в процессе
**Последнее обновление:** 2026-11-13

## Что сделано

- 2026-11-06: the read-only legacy agency Direct check passed. The request used `YD_OAUTH_TOKEN` and `Client-Login`. Direct returned the configured advertiser login with a ClientId. [Agency smoke result](development-history/2026-11-06-2052-legacy-agency-smoke-pass.md)
- 2026-11-06: read-only agency and Audience smoke tests confirmed the expected segment for registry entry 1. The agency request used the intended token and header, but Direct returned error 8800. [Smoke results](development-history/2026-11-06-2100-agency-audience-smoke.md)
- 2026-11-06: fixed both OAuth review defects. An explicit empty legacy selector retains `YD_LOGIN` routing, and Direct discovery rejects malformed nonempty Login entries. Corrected `.env.example` and added frozen pre-change schema fingerprints. All 286 offline checks passed. [Review fixes](development-history/2026-11-06-2042-yandex-oauth-review-fixes.md)
- 2026-11-06: verified the privately supplied expected Metrica counter through all three registry routes. Entries 2/3 returned the matching ID with edit access; entry 1 returned 403. Selected-token headers matched in every case. This establishes expected-resource routing and an account access difference, not read-only OAuth grants. [Counter smoke results](development-history/2026-11-06-2024-yandex-oauth-counter-smoke.md)
- 2026-11-06: ran authorized read-only smoke checks using local `.env`. All three Direct registry routes matched configured logins and separately read ClientIds; all accounts are SUBCLIENT. Metrica reads succeeded (edit/own permissions); Audience succeeded for two entries and returned 403 for one. Legacy agency and limited-permission resource acceptance remain unverified. [Live smoke results](development-history/2026-11-06-2023-yandex-oauth-live-smoke.md)
- 2026-11-06: final OAuth diff review found one stale configuration comment in `.env.example:28`; no additional routing defect was identified in the examined paths. Fresh offline run passed all 261 checks and `git diff --check` passed. Live identity/resource checks remain blocked on controlled credentials and expected identities. [Final review and smoke matrix](development-history/2026-11-06-2018-yandex-oauth-final-review.md)
- 2026-11-06: Shared OAuth Tasks 1–4 are implemented. Legacy Direct inventory enforces the allowlist per probe. `yd_yandex_accounts_get` lists configured accounts offline and can verify one account's read access under a shared deadline. All 261 offline checks passed. [Transport checks](development-history/2026-11-06-2008-yandex-oauth-http-routes.md) · [Discovery](development-history/2026-11-06-2014-yandex-oauth-service-discovery.md)
- 2026-11-06: revised the shared OAuth plan to preserve the discovery filter, keep legacy-only schemas, and bound live verification to three requests and a shared five-second deadline. Earlier setup checks are complete and the one-time checklist was removed. [Plan review follow-up](development-history/2026-11-06-1900-yandex-oauth-plan-revisions.md)
- 02–04.10.2026: обнаружена заготовка будущего PR в форке @Tahionnn (ветка
  `feat/ad-images-file-path`: фичи file_path/url для `yd_ad_images_add` + переезд на
  uv/src-layout/Docker); владелец оставил [комментарий-приглашение](https://github.com/Tahionnn/yandex-ads-mcp/commit/152dd935f387f8c662974aa1c2d52ec18b054001#commitcomment-203096506)
  открыть PR и разделить фичи от перестройки. Заведена облачная рутина-дозор
  «yandex-ads-mcp: дозор GitHub» (`trig_014BWn665GyxS3DvhqskwDsZ`, пн+чт 09:00 Нск,
  read-only) — отчёты на <https://claude.ai/code/routines>. Подробности —
  `development-history/2026-10-04-2210-fork-outreach-watch.md`.
- Релиз 1.3.0 (2026-09-14): принят внешний PR #2 (@sev222) — комбинаторные объявления
  (`ResponsiveAd`, API v501), длинные ID объявлений строками, настройки автотаргетинга
  (`yd_keywords_update` — 152-й инструмент), починка `yd_bid_modifiers_get` (падал с ошибкой
  8000 на любой вызов). Read-only пути проверены на живом кабинете перед мержем.
  Следом дедуплицирован `_api501` (`server.py` делегирует в `tools_direct_extra.py`),
  About репозитория обновлён до «152 tools». Подробности —
  `development-history/2026-09-14-1011-pr2-review-release.md`.
- 02.09.2026: зеркала скиллов и хуки в `.claude/` теперь коммитятся (`.gitignore`: `.claude/*` с исключениями `skills/`, `hooks/`); зеркала — копии каталогов, не symlink (Portainer не клонирует symlink).

- Релиз 1.2.0 (2026-08-06): несколько кабинетов Директа без агентства через `YD_DIRECT_TOKENS`,
  инструмент `yd_direct_accounts_get`, единая `direct_headers()`.
- 1.1.x: Яндекс Аудитории (23 инструмента), сегменты Аудиторий в ретаргетинге, per-service
  токены, Api-Key для Wordstat.
- 2026-09-02: инициализирован cmit-starter 2026.09.02 на ветке `starter-upgrade-2026.09.02`.
  Применено: `AGENTS.md`, `CLAUDE.md`, `agent_docs/` (гайды, шаблоны, snapshot/backlog/
  architecture/invariants/glossary/operations), скиллы `/handoff` и `/starter-upgrade`
  с зеркалами, хуки (не подключены), `.editorconfig`, `.cursorignore`, markdownlint.
  Отложено вручную: см. «Не проверено».

## Что в процессе

- Удалённый HTTP-деплой на Dokploy (`yandex.riddler.agency`, ветка `feat/remote-deploy`):
  Dockerfile с mcp-proxy и документация готовы, образ собран локально. Открытые шаги:
  smoke-тест контейнера, PR в `codex/custom`, создание приложения и деплой в Dokploy,
  секреты и Basic Auth в UI владельцем, проверка эндпоинта. Конфигурация и грабли —
  `guides/operations.md`, решение — `adr/2026-11-13-2234-http-bridge-dokploy.md`.
- Ожидание реакции @Tahionnn на приглашение открыть PR (комментарий от 02.10 в его форке,
  по состоянию на 04.10 ответа и PR нет). Следит облачная рутина-дозор; при появлении PR —
  ревью по методике из `development-history/2026-09-14-1011-pr2-review-release.md`.

## Известные проблемы

- The original OAuth review findings are resolved. The plan now distinguishes the public MCP string schema from the internal handler's `None` handling. [Follow-up verification](development-history/2026-11-06-2045-yandex-oauth-fix-verification.md)

## Тупики

<!-- Что пробовали и почему не сработало. Незаписанный тупик следующая сессия проходит заново
     за ту же цену. Формат: «пробовали X → не сработало, потому что Y». -->

## Не проверено

- A CLIENT-type standalone Direct account remains unverified. The successful Metrica counter reads reported edit access. [Counter smoke results](development-history/2026-11-06-2024-yandex-oauth-counter-smoke.md)
- `deploy.yml` из стартера намеренно не создан: удалённый сервис деплоится через Dokploy
  (Dockerfile, не Portainer-стек). Если появится Portainer-стек — включать по `agent_docs/guides/autodeploy.md`.
- Хуки `state-freshness.sh` и `handoff-nudge.sh` оставлены выключенными; подключаются
  осознанно по [гайду](guides/context-management.md).
- Стартовые инварианты в `agent_docs/invariants.md` переписаны под проект по README
  и `test_safety.py`; владелец их не подтверждал.
- Рутина-дозор не видит ответы на комментарий в чужом форке (GitHub API для сторонних
  репозиториев из облачной сессии закрыт — см. operations.md); ответ Tahionnn надёжно
  ловится только локальной проверкой или появлением PR.

## Следующие шаги

- Complete the standalone Direct CLIENT check with an appropriate credential and independently expected identity. [Smoke matrix](development-history/2026-11-06-2018-yandex-oauth-final-review.md)
