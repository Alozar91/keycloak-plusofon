# Концепция: авторизация в Keycloak через обратный Flash Call (Plusofon)

> Статус: **концепция утверждена частично** — интеграция выбрана (SPI). Код не пишется до
> утверждения деталей ниже.
> Сценарий: пользователь оставляет свой номер → система показывает номер для звонка →
> пользователь **сам звонит** на этот номер со своего телефона → владение номером
> подтверждено → Keycloak завершает аутентификацию.

---

## 0. Принятые решения

- **Интеграция с Keycloak: вариант A — Custom Authenticator SPI**, по схеме «тонкий SPI +
  внешний сервис верификации» (A2, см. §3).
- **Keycloak запускается в Docker** (образ quay.io/keycloak/keycloak, свежая 26.x, start-dev).
- **Стек тестового приложения:** backend — Python/FastAPI, frontend — React (Vite).
- **Стартуем без ключей Plusofon:** этап 1 полностью на mock-режиме (симуляция звонка),
  продовые креды подключаются позже переключением `MOCK=0`.
- Этап 1: тестовое веб-приложение (сервис верификации + UI + mock-режим) — автономно.
- Этап 2: SPI-провайдер для Keycloak, ходящий в сервис этапа 1.

---

## 1. Что выяснено в API Plusofon (help.plusofon.ru/api/v1/flash-call)

База: `https://restapi.plusofon.ru`, заголовки: `Authorization: Bearer {token}`, `Client: 10553`.

**Два вида токенов:**
| Токен | Где взять | Для чего |
|---|---|---|
| Основной токен ЛК | ЛК Плюсофон → «Разработчикам → Доступ к API» | Управление аккаунтами/настройками/пакетами Flash Call |
| `access_token` аккаунта Flash Call | `POST /api/v1/flash-call` (создание аккаунта) или `GET /api/v1/flash-call` (список) | Отправка вызовов и проверок: `send`, `check`, **`call-to-auth`** |

**Ключевые методы:**
| Метод | Назначение |
|---|---|
| `POST /api/v1/flash-call` | Создать аккаунт Flash Call → `{account_id, access_token}` |
| `GET /api/v1/flash-call/proposals` | Список пакетов; у обратных пакетов флаг `flashcall_reverse: true` |
| `PUT /api/v1/flash-call/proposals/{account_id}` | Подключить пакет; для обратного FC — параметр `number`: номер 8-800, на который будут звонить пользователи |
| `POST /api/v1/flash-call/call-to-auth` | **Обратный Flash Call.** Body: `{phone, hook_url}`. Ответ: `{data: {key, phone}}`, где `phone` — номер, который показать пользователю для звонка |
| Вебхук | После звонка пользователя → `POST` на `hook_url` с адреса `185.54.48.1`, тело: `{phone, key}` |

Важные детали:
- `hook_url` можно передать **в каждом запросе** `call-to-auth` — удобно для тестовых туннелей.
- Вебхук приходит на `hook_url` с IP `185.54.48.1`.
- Пакеты платные (порядка 0,35–0,4 ₽/вызов у обычных FC; тариф обратных — уточнить в ЛК/поддержке).

---

## 2. Сквозной сценарий (в проде, со SPI)

```
Браузер            Keycloak (SPI)          Сервис верификации        Plusofon        Телефон
  │ логин → шаг FC       │                       │                      │               │
  ├─────────────────────►│                       │                      │               │
  │                      │ POST /api/verify/start│                      │               │
  │                      │  {phone, session_ref}─►│                      │               │
  │                      │                       │ POST call-to-auth ──►│               │
  │                      │                       │◄── {key, phone_auth} ┤               │
  │ страница «позвоните  │                       │                      │               │
  │ на 8-800-…» + опрос  │                       │                      │               │
  │◄─────────────────────┤                       │                      │               │
  │                      │                       │                      │  звонит       │
  │                      │                       │                      │  с phone ◄────┤
  │                      │                       │◄─ POST {phone, key} ─┤  (webhook)    │
  │                      │ poll: verified ✓      │                      │               │
  │◄── редирект, вход ───┤  user по phone,       │                      │               │
  │                      │  атрибуты phone*,     │                      │               │
  │                      │  context.success()    │                      │               │
```

---

## 3. Архитектура при выбранном SPI (схема «тонкий SPI + сервис»)

```
[Браузер] ⇄ [Keycloak + FlashCallAuthenticator (Java)] ⇄ [Сервис верификации] ⇄ [Plusofon API]
                                                            ▲
                                                            └── вебхук от Plusofon (185.54.48.1)
```

Почему SPI оставляем тонким, а Plusofon-логику выносим в сервис:
- mock-режим и отладка живут в сервисе и не требуют пересборки JAR;
- сервис переиспользуем (админ-боты, другие системы);
- вебхук не обязан висеть на публичном адресе самого Keycloak;
- SPI-код минимален: UI-страница + 2 HTTP-вызова.

### 3.1 Поведение аутентификатора (Java)

Флоу: `Browser flow → … → [Flash Call Verify (REQUIRED)] → вход`.

1. `authenticate()`:
   - если пользователь уже известен и настроен `skipIfVerified` (атрибут `phone_verified=true`)
     → `attempted/excluded` (шаг пропускается);
   - если телефон ещё неизвестен (шаг стоит до ввода учётных данных) → показать форму
     ввода телефона (свой FTL-шаблон);
   - вызвать сервис `POST /api/verify/start {phone, session_ref=authSessionId}` →
     сохранить `verification_id` в auth session notes (идемпотентность);
   - отрендерить страницу «позвоните на номер» с таймером и JS-опросом.
2. Опрос статуса: JS дергает публичный REST того же JAR
   (`/realms/{realm}/flashcall/verify-status?...` — `RealmResourceProvider`), тот
   проксирует `GET /api/verify/{id}/status` сервиса. Ответ: `pending | verified | expired`.
3. По `verified` JS сабмитит форму → Keycloak вызывает `action()`:
   - повторно проверить `verified` у сервиса (не доверять только клиенту);
   - найти пользователя по телефону (атрибут `phoneNumber`) или username=phone;
     при `userMode=create-if-missing` — создать;
   - проставить атрибуты: `phoneNumber`, `phone_verified=true`, `phone_verified_at`;
   - `context.success()` — вход завершён.
4. Истёк TTL → кнопка «запросить новый звонок» (повторный `start`, старый `key` гасится).

### 3.2 Конфигурация аутентификатора (админка KC)

| Параметр | По умолчанию | Смысл |
|---|---|---|
| `verificationServiceUrl` | — | База REST сервиса верификации |
| `serviceApiKey` | — | Shared secret, заголовок `X-Api-Key` на вызовах SPI→сервис |
| `userMode` | `existing-only` | `existing-only` / `create-if-missing` |
| `phoneAttribute` | `phoneNumber` | Атрибут пользователя с телефоном |
| `skipIfVerified` | `true` | Пропускать шаг, если телефон уже верифицирован |
| `ttlSeconds` | `180` | Отображаемый таймер (реальный TTL контролирует сервис) |

---

## 4. Этап 1 — тестовое веб-приложение (сервис верификации)

```
kc-plusofon/
├── app/                    # сервис верификации
│   ├── POST /api/verify/start        {phone, session_ref?} → {verification_id, call_number, ttl}
│   ├── GET  /api/verify/{id}/status   → {status: pending|verified|expired}
│   ├── POST /webhook/plusofon        {phone, key} — проверка IP 185.54.48.1, одноразовый key
│   └── POST /api/dev/simulate        [MOCK=1] имитация звонка: прислать {key, phone} как вебхук
├── web/ (или static в app) # страница: ввод номера → показ номера для звонка → опрос → успех
├── keycloak/               # этап 2: SPI-проект (Maven/Gradle), realm-импорт, флоу
├── docker-compose.yml      # app + keycloak + (опц.) cloudflared/ngrok для webhook
└── .env.example            # PLUSOFON_API_TOKEN, FC_ACCOUNT_TOKEN, MOCK=1|0, BASE_URL, API_KEY
```

- Хранение: SQLite или in-memory на MVP: `{verification_id, key, phone, session_ref,
  status, created_at, expires_at}`; TTL 120–180 c, key одноразовый.
- Auth между SPI и сервисом: заголовок `X-Api-Key`.
- Mock-режим (`MOCK=1`): `call-to-auth` не вызывается, key генерируется локально, номер
  для звонка — фиктивный; звонок эмулируется `POST /api/dev/simulate` (или кнопкой в UI).

---

## 5. Безопасность и ограничения

- **Спуфинг caller ID** — главный риск: факт = «звонок с заявленного номера», а A-номер в
  ТфОП подделываем. Атака: атакующий сам создаёт запрос на номер жертвы и звонит на 8-800
  с подделанным A-номером. Митигция: короткий TTL, одноразовый `key`, лимиты на
  `start` (сервис + лимиты Plusofon `calls_per_user_*`), мониторинг. Для высокой
  гарантии — комбинировать с OTP.
- **Вебхук**: allowlist IP `185.54.48.1`, быстрый ответ 200, идемпотентность, принимать
  только `key` существующей pending-записи; `hook_url` по HTTPS.
- **Секреты**: `access_token` аккаунта FC — только в env сервиса, не в KC и не в UI.
- Ссылка SPI↔сервис — внутренняя сеть/VPN; наружу светится только KC и (для вебхука) сервис.
- Rate limits Plusofon: уточнить, действуют ли `calls_per_*` на `call-to-auth`.

---

## 6. Предпосылки / открытые вопросы

1. **Версия Keycloak** и способ запуска (docker?) — от этого зависит каркас SPI
   (Quarkus-based KC ≥ 17 vs legacy).
2. **Стек сервиса**: Python (FastAPI) или Node (Express) — нужно решение.
3. **Plusofon**: есть ли токен ЛК, аккаунт Flash Call, пакет `flashcall_reverse` и 8-800?
   Если нет — стартуем на mock-режиме, продовые креды подключим позже.
4. **Публичный URL для вебхука** на время теста (cloudflared/ngrok или тестовый VPS).

Открытые технические вопросы (проверить практически):
- приходит ли вебхук `call-to-auth` сразу при входящем звонке (можно сбросить) или после
  N секунд/ответа;
- всегда ли возвращается один и тот же `phone` (подключённый 8-800) или возможен пул
  номеров на сессию;
- поведение при повторном запросе на тот же номер до истечения прошлого.
