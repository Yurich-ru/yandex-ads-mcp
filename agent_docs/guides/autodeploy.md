# Автодеплой: стандарт (GitHub → Portainer)

Единый способ доставки кода в бой для проектов сети. Конкретика проекта (имена контейнеров,
адреса, команды) — в `agent_docs/guides/operations.md`; здесь — **стандарт и как его включить**.

## Принцип

**Push в `main` = деплой.** Промежуточных ручных шагов нет, обратимость — новым коммитом.
Единственный гейт — зелёный CI: красный код до прода не доезжает.

```
git push origin main
   ↓  .github/workflows/deploy.yml, job test
тесты/линт (гейт)
   ↓  job deploy (needs: test)
POST $PORTAINER_WEBHOOK_URL  → Portainer ПЕРЕСОБИРАЕТ стек из git
   ↓  пауза
health-гейт по SSH: контейнер running (+ health-эндпоинт) → зачистка старых образов
```

- `[skip ci]` в сообщении коммита пропускает и тесты, и деплой — для правок, не меняющих образ
  (доки, `agent_docs/**`).
- Миграции/сборку применяет контейнер на старте (entrypoint), отдельного шага в CI нет:
  **миграция едет вместе с кодом** — значит только add-only (см. INV о деструктивных миграциях).
- Один Dockerfile на все роли сервиса; роль выбирается переменной `ROLE` в entrypoint.

## Соглашения (нарушение = сломанный деплой)

| Правило | Почему |
|---|---|
| Имя стека = короткое имя проекта; контейнеры `<стек>-<роль>` | предсказуемость для health-гейта и зачистки образов (фильтр по метке compose-проекта) |
| **Новому сервису НЕ давать свой `build:`** — переиспользовать уже собираемый образ (`image: <проект>-<основной сервис>:latest`) + свой `ROLE` | иначе compose назовёт образ `<проект>-<сервис>`, которого нет в реестре, и любой pull-путь падает `pull access denied` |
| **Не ставить `depends_on: <svc>: condition: service_healthy`** | вместе с новым healthcheck ломает webhook-redeploy стека (webhook принят, стек не обновляется). Делать сервис устойчивым ретраем |
| Каждую переменную окружения **явно прописать** в нужном сервисе compose (`- VAR=${VAR:-}`) | панель подставляет значение только туда, где переменная перечислена; иначе внутри контейнера пусто |
| Производные значения (URL БД из пароля и т.п.) собирать в compose, а не задавать в env стека | два источника истины расходятся молча |
| `mem_limit` на каждый контейнер | один сервис не должен съесть хост |

**Webhook СОБИРАЕТ, кнопка «Pull and redeploy» ПУЛЛИТ.** Кнопку в UI не нажимать: наши образы
собираются локально и в реестр не пушатся. Деплой — только через webhook (его зовёт CI).

## Что нужно настроить один раз

### 1. GitHub (агент может сам, если есть `gh`)

| Что | Имя | Значение |
|---|---|---|
| Секрет | `PORTAINER_WEBHOOK_URL` | URL вебхука стека (см. шаг 2) |
| Секрет | `SSH_HOST` / `SSH_USER` / `SSH_KEY` | доступ к хосту для health-гейта |
| Переменная | `DEPLOY_ENABLED` | `true` — включает деплой |
| Переменная | `SSH_CHECK_ENABLED` | `true` — включает пост-деплой проверку |
| Переменная | `HEALTH_CONTAINER` / `STACK_NAME` | имя контейнера для проверки / имя стека |

```bash
gh secret set PORTAINER_WEBHOOK_URL --repo <owner>/<repo> --body "https://<portainer>/api/stacks/webhooks/<uuid>"
# Переменные: в старом gh нет `gh variable` — через API:
gh api -X POST repos/<owner>/<repo>/actions/variables -f name=DEPLOY_ENABLED -f value=true
gh api repos/<owner>/<repo>/actions/variables --jq '.variables[] | "\(.name)=\(.value)"'   # проверка
```

> Грабля: `gh secret set X --body -` записывает **литерал `-`**, а не stdin. Передавать значение
> строкой (`--body "..."`) или файлом (`--body-file`), затем проверять реальным вызовом.

### 2. Portainer (агент может сам — нужен API-токен)

Токен: в Portainer → *My account* → *Access tokens* → *Add access token* (значение показывается
один раз). Дальше все вызовы с заголовком **`X-API-Key: <token>`**.

> Токен = управление контейнерами хоста. Хранить вне репозиториев (например,
> `~/.config/cmit/deploy.env`), никогда не коммитить, при подозрении — отозвать в UI одним кликом.
> По возможности — отдельный пользователь Portainer с правами только на нужное окружение
> (права токена = права его пользователя).

```bash
source ~/.config/cmit/deploy.env      # PORTAINER_URL, PORTAINER_API_TOKEN
H="X-API-Key: $PORTAINER_API_TOKEN"

curl -s -H "$H" "$PORTAINER_URL/api/endpoints" | jq '.[] | {Id, Name}'        # → endpointId
curl -s -H "$H" "$PORTAINER_URL/api/stacks"    | jq '.[] | {Id, Name, AutoUpdate}'
# Создание стека из git: POST /api/stacks/create/standalone/repository?endpointId=<N>
#   тело: имя стека, URL репозитория, ветка, путь к compose, env-переменные, autoUpdate(webhook),
#   для приватного репо — учётные данные git.
# Точные поля тела сверять со swagger своей версии Portainer (у инстанса /api/docs может не быть;
# версия — GET /api/system/status, публичный).
```

Webhook стека: UUID лежит в поле `AutoUpdate.Webhook`, полный URL —
`<PORTAINER_URL>/api/stacks/webhooks/<uuid>`; его и класть в секрет GitHub. Триггер — `POST` на этот URL.

### 3. Остаётся человеку

- Выпуск самого API-токена Portainer (нужен пароль пользователя) и SSH-ключа для health-гейта.
- DNS-запись поддомена и proxy-host + TLS в reverse-proxy (если не автоматизировано отдельно).
- Значения секретов приложения (ключи интеграций) — в env стека.

## Проверка, что стандарт включён

- [ ] `gh api repos/<owner>/<repo>/actions/variables` показывает `DEPLOY_ENABLED=true`
- [ ] Тестовый коммит в `main` → `gh run list --workflow deploy.yml` зелёный
- [ ] На хосте контейнер пересоздан (сравнить `docker ps` / версию схемы), а не только «webhook принят»
- [ ] `SSH_CHECK_ENABLED=true` — иначе пост-деплой гейта нет и сбой деплоя останется незамеченным
- [ ] Раздел «Деплой» в `operations.md` заполнен конкретикой проекта
