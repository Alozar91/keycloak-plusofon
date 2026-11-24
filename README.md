# Тестовый стенд: авторизация в Keycloak через обратный Flash Call (Plusofon)

Полная концепция — [`docs/CONCEPT.md`](docs/CONCEPT.md).

**Суть:** пользователь вводит номер → сервис показывает номер для звонка → пользователь
звонит на него со своего телефона → Plusofon присылает вебхук → номер подтверждён.
На этапе SPI (этап 2) этим сервисом управляет кастомный аутентификатор Keycloak.

## Состав

```
app/      — backend: FastAPI (API верификации, вебхук Plusofon, mock-режим, SQLite)
web/      — frontend: React + Vite (ввод номера → «позвоните на номер» → опрос → успех)
certs/    — копия TLS-сертификата системного nginx (merch-vibe.ru, вне git) —
            используется контейнерами web и keycloak (TLS терминируют они сами)
keycloak/ — этап 2: SPI-провайдер (аутентификатор phone-call-auth)
docs/     — концепция
```

## Быстрый старт (Docker)

```bash
cp .env.example .env        # настройки по умолчанию уже рабочие для mock-режима
docker compose up --build
```

- Frontend: **https://merch-vibe.ru:4443** (локально также http://localhost:3000)
- Keycloak: https://merch-vibe.ru:8443 (admin/admin; TLS — копия сертификата системного
  nginx в `certs/`, issuer = `https://merch-vibe.ru:8443`). Для локальной отладки доступен
  и http://localhost:8080 (issuer не совпадает — только для curl-тестов)
- Backend API напрямую: http://localhost:8000/api/health

В mock-режиме на странице появится кнопка **«Симулировать звонок»** — она выполняет то же
действие, что и вебхук Plusofon после реального звонка, поэтому весь цикл проверяется
без телефона и без ключей.

## Быстрый старт (без Docker, для разработки)

```bash
# backend
cd app
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --port 8000          # из каталога app/

# frontend (в втором терминале)
cd web
npm install
npm run dev                                     # http://localhost:5173, прокси на :8000
```

## API сервиса

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/api/verify/start` | `{phone, session_ref?}` → `{verification_id, call_number, ttl, mock}` |
| `GET` | `/api/verify/{id}/status` | `{status: pending\|verified\|expired, ttl_left}` (поллинг) |
| `POST` | `/webhook/plusofon` | вебхук Plusofon `{phone, key}` (IP allowlist, одноразовый key) |
| `POST` | `/api/dev/simulate` | только MOCK: имитация звонка `{verification_id}` |
| `GET` | `/api/health` | статус, mock on/off |

Если задан `API_KEY`, запросы к `/api/verify/*` требуют заголовок `X-Api-Key`
(так будет ходить SPI из Keycloak).

## Реальный Plusofon (подключен и работает)

Стенд работает в боевом режиме (`MOCK=0`) с аккаунтом Flash Call **2630 («test»)**:

- основной токен ЛК — `.env:PLUSOFON_LK_TOKEN` (для управления: `scripts/setup_flashcall.py`)
- токен аккаунта — `.env:PLUSOFON_FC_ACCOUNT_TOKEN` (им ходят `call-to-auth`; ВАЖНО: токен
  аккаунта НЕ подходит к методам управления — отсюда 403 «Not enough permissions»)
- пакет «Обратный Flash Call» 100 вызовов, активен до ~2026-12-23; номер для звонка
  выдаёт сам Плюсофон в ответе `call-to-auth` (поле `phone`, не 8-800)
- настройки аккаунта: все операторы разрешены, `token_lifetime_sec=300`,
  `hook_url=https://merch-vibe.ru:4443/webhook/plusofon`

Кнопка «Симулировать звонок» при `MOCK=0` недоступна (сервис вернёт 403).
Второй аккаунт 2632 («aut») с таким же пакетом — кандидат на отключение в ЛК
(иначе двойная абонплата).

### Публичный URL для вебхука

`PUBLIC_BASE_URL=https://merch-vibe.ru:4443` в `.env` — backend сам подставляет полный
`hook_url` в каждый запрос `call-to-auth`; вебхук: `https://merch-vibe.ru:4443/webhook/plusofon`
(allowlist IP `185.54.48.1`). HTTPS терминирует nginx внутри web-контейнера (порт 443 →
host 4443) с копией сертификата системного nginx (`certs/`) — системный nginx хоста
не участвует и не модифицируется.

ВНИМАНИЕ: домен `merch-vibe.ru` на 80/443 обслуживается системным nginx и занят другими
проектами (`/chat/`, `/chicken/`, `/vibe-files/`) — НЕ вешать стенд на 80/443 системного
nginx. Туннель cloudflared в compose оставлен закомментированным как запасной вариант.

### Синхронизация пользователей при входе с фронта

Отдельный фронт (`:4443`) не проходит через Keycloak, поэтому сервис верификации сам
создаёт пользователя в realm после подтверждения звонка (`app/kcsync.py`):
вебхук/simulate → verified → Admin API Keycloak → пользователь с атрибутом
`phoneNumber` (идемпотентно: сначала поиск, потом создание). Управляется
`KC_SYNC` (вкл/выкл), реквизиты админа берутся из `KEYCLOAK_ADMIN`/`KEYCLOAK_ADMIN_PASSWORD`.

## Безопасность (кратко)

- `X-Api-Key` на `/api/verify/*` (для SPI), allowlist IP `185.54.48.1` на вебхуке,
  одноразовый `key`, TTL 180 c, rate-limit на `start` (5 запросов / 10 мин на номер).
- Подробный разбор рисков (спуфинг caller ID и т.п.) — `docs/CONCEPT.md`, §5.

## Этап 2 (Keycloak SPI) — работает

Кастомный аутентификатор **`phone-call-auth`** (`keycloak/` → `phone-verify-spi.jar`):

1. Показывает форму ввода телефона (`phone-phone-form.ftl`), нормализует номер (8… → 7…).
2. Вызывает сервис верификации `POST /api/verify/start` (`session_ref` = id parent auth session),
   показывает страницу «позвоните на номер» с отсчётом TTL и автосабмитом каждые 4 с.
3. Опрашивает `GET /api/verify/{id}/status`; на `verified` ищет пользователя по атрибуту
   `phoneNumber`, при отсутствии — создаёт (конфиг `userMode=create|require`) и завершает вход.

### Сборка и настройка realm

```bash
docker run --rm -v "$PWD/keycloak":/src -v kc-maven-cache:/root/.m2 -w /src \
  maven:3.9-eclipse-temurin-17 mvn -q package     # → keycloak/target/phone-verify-spi.jar
# jar монтируется в /opt/keycloak/providers/ (см. docker-compose.yml)
./scripts/setup_keycloak.sh   # пересоздаёт realm plusofon-test + flow phone-browser
```

Скрипт (идемпотентно, через admin API):
- realm `plusofon-test` (+ `unmanagedAttributePolicy=ADMIN_EDIT` — иначе атрибут `phoneNumber`
  не сохранится, профиль KC 26 вырезает неуправляемые атрибуты);
- flow `phone-browser` = [Cookie ALTERNATIVE] → [Phone Call Verification (Plusofon) ALTERNATIVE]
  — ВАЖНО: в KC 26 аутентификатору нельзя ставить CONDITIONAL (ломает флоу: UNKNOWN_USER),
  а REQUIRED рядом с ALTERNATIVE игнорируется;
- конфиг аутентификатора: `verificationServiceUrl=http://app:8000` (внутри docker-сети),
  `serviceApiKey=$API_KEY`, `userMode=create`;
- привязка flow как `browserFlow` realm'а.

### Проверка

`https://merch-vibe.ru:8443/realms/plusofon-test/account` → форма телефона → звонок →
вход. Сквозной тест (форма → звонок → вебхук → вход → пользователь с `phoneNumber` →
повторный вход тем же номером без дублей) выполнен 2026-11-23 и зелёный.

При первом входе Keycloak показывает стандартный VERIFY_PROFILE (имя/фамилия) — один раз.
Каждый вход расходует один вызов пакета (~99 осталось до 2026-12-23).
