# Авторизация через обратный Flash Call: полная цепочка

Документ описывает сквозной процесс входа: браузер → Keycloak (SPI) → сервис
верификации (FastAPI) → Plusofon → телефон пользователя → вебхук → токены Keycloak.

Компоненты:

| Компонент | Технология | Роль |
|---|---|---|
| Keycloak | KC 26 + SPI `phone-verify-spi.jar` | организация входа (browser flow), страницы, поиск/создание пользователя, выдача токенов |
| Сервис верификации | FastAPI (`app/`) | API верификации, вызов Plusofon, приём вебхука, SQLite-хранилище |
| Plusofon | REST API `restapi.plusofon.ru` | метод «Обратный Flash Call» (`call-to-auth`): выдаёт номер для звонка и `key`, присылает вебхук после звонка |
| Frontend (standalone) | React (`web/`) | отдельный тестовый вход без Keycloak (та же пара вызовов `start`/`status`) |

---

## 1. Общая схема

```
Браузер                Keycloak (SPI)              FastAPI-сервис             Plusofon          Телефон
   │  GET /realms/plusofon-test/login-actions/authenticate   │                        │                │
   │──────────────►│                                          │                        │                │
   │  HTML: форма ввода телефона                              │                        │                │
   │◄──────────────│                                          │                        │                │
   │  POST phone=7999...                                     │                        │                │
   │──────────────►│  POST /api/verify/start {phone, session_ref}                      │                │
   │               │─────────────────────────────────────────►│  POST /api/v1/flash-call/call-to-auth   │
   │               │                                          │───────────────────────►│                │
   │               │  {verification_id, call_number, ttl}     │      {key, phone}      │                │
   │               │◄─────────────────────────────────────────│◄───────────────────────│                │
   │  HTML: «позвоните на номер» (автосабмит каждые 4 с)      │                        │                │
   │◄──────────────│                                          │                        │   входящий     │
   │               │                                          │                        │     звонок ◄───┤
   │  POST (пустая форма, каждые 4 с)                         │  POST /webhook/plusofon {key}           │
   │──────────────►│                                          │◄───────────────────────│                │
   │               │  GET /api/verify/{id}/status             │  → status = verified   │                │
   │               │─────────────────────────────────────────►│                        │                │
   │               │◄─────────────────────────────────────────│                        │                │
   │               │  поиск/создание пользователя по атрибуту phoneNumber              │                │
   │               │  ctx.setUser(user); ctx.success()        │                        │                │
   │  redirect + code → обмен на токены (стандарт OIDC)       │                        │                │
   │◄──────────────│                                          │                        │                │
```

Суть метода: пользователь звонит **сам** на номер, выданный Plusofon («обратный
flash call»). Подтверждением владения номером служит факт звонка — Plusofon
сопоставляет его с выданным ранее одноразовым `key` и уведомляет сервис вебхуком.
Разговор не происходит: сброс после первого гудка.

---

## 2. Роль Keycloak

Keycloak здесь — «каркас» входа, всё нестандартное вынесено в SPI-аутентификатор:

- **Browser flow** realm'а `plusofon-test` — `phone-browser`:
  `[Cookie ALTERNATIVE] → [Phone Call Verification (Plusofon) ALTERNATIVE]`.
  Cookie-исполнение срабатывает для уже залогиненных; иначе управление получает SPI.
- **Аутентификатор `phone-call-auth`** (`PhoneCallAuthenticatorFactory`, ID =
  `phone-call-auth`) рисует свои страницы через FreeMarker-шаблоны
  (`phone-phone-form.ftl`, `phone-call-form.ftl`) и ходит в сервис верификации
  HTTP-клиентом (`VerificationClient`).
- **Состояние** между запросами хранится в auth notes authentication session
  (`phoneVerifyId`, `phoneVerifyPhone`, `phoneVerifyCallNumber`, `phoneVerifyStarted`,
  `phoneVerifyTtl`) — это позволяет аутентификатору быть stateless между
  перезагрузками страницы.
- **Пользователи**: ищутся по атрибуту `phoneNumber`; при отсутствии создаются
  (конфиг `userMode=create`; `require` — только существующие). Для сохранения
  атрибута в KC 26 на realm включён `unmanagedAttributePolicy=ADMIN_EDIT`.
- **Завершение**: после `ctx.setUser(user); ctx.success()` дальше всё стандартно —
  Keycloak редиректит с кодом, клиент обменивает его на access/refresh/ID token.
- После первого входа KC показывает стандартный VERIFY_PROFILE (имя/фамилия).

Ограничения KC 26, учтённые в конфигурации: аутентификатору нельзя ставить
`CONDITIONAL` (ломает флоу: UNKNOWN_USER); `REQUIRED` рядом с `ALTERNATIVE`
игнорируется — поэтому оба исполнения `ALTERNATIVE`.

Конфиг аутентификатора (задаётся в админке, см. `PhoneCallAuthenticatorFactory`):
`verificationServiceUrl` (по умолчанию `http://app:8000` — внутри docker-сети),
`serviceApiKey` (совпадает с `API_KEY` из `.env` сервиса), `userMode`.

---

## 3. Пошагово: порядок вызовов API

### Шаг 0. Начало входа
Браузер открывает `https://merch-vibe.ru:8443/realms/plusofon-test/account` (или
любой OIDC-клиент realm'а). Keycloak запускает flow, доходит до SPI,
`authenticate()` → нет заметок, нет формы → отдаётся `phone-phone-form.ftl`.

### Шаг 1. Ввод телефона (браузер → Keycloak)
`POST ${url.loginAction}` с полем `phone`. SPI нормализует номер
(`normalizePhone`: `8…`/`+7…`/10 цифр → `7XXXXXXXXXX`, иначе ошибка на форме).

### Шаг 2. Старт верификации (SPI → сервис)
`VerificationClient.start()`:

```
POST {verificationServiceUrl}/api/verify/start
Headers: Content-Type: application/json, X-Api-Key: {serviceApiKey}
Body:    {"phone": "79991234567", "session_ref": "<id parent auth session>"}
→ 200 {"verification_id": "...", "call_number": "+7 (939) ...", "ttl": 180, "mock": false}
```

`session_ref` — id родительской auth session Keycloak (для трассировки в БД сервиса).

Внутри сервиса (`main.py: start_verification`):
1. `require_api_key` — сверка `X-Api-Key` (если `API_KEY` задан).
2. `normalize_phone` + rate-limit: не более 5 `start` на номер за 600 с (429).
3. Если `MOCK=1` — ключ генерируется локально (`mock-…`), номер из `MOCK_CALL_NUMBER`.
   Иначе `PlusofonClient.call_to_auth(phone)` (шаг 3).
4. Запись в SQLite (`store.create`): `id`, одноразовый `key`, phone, `call_number`,
   `status=pending`, `expires_at = now + CALL_TTL (180 c)`.

### Шаг 3. Запрос к Plusofon (сервис → Plusofon)
```
POST https://restapi.plusofon.ru/api/v1/flash-call/call-to-auth
Headers: Authorization: Bearer {PLUSOFON_FC_ACCOUNT_TOKEN}, Client: 10553
Body:    {"phone": "79991234567", "hook_url": "https://merch-vibe.ru:4443/webhook/plusofon"}
→ 200 {"success": true, "data": {"key": "...", "phone": "79395555028"}}
```

Важно: используется **токен аккаунта Flash Call** (`PLUSOFON_FC_ACCOUNT_TOKEN`),
не основной токен ЛК. Номер для звонка возвращает сам Плюсофон (поле `data.phone`).

### Шаг 4. Страница ожидания (Keycloak → браузер)
SPI сохраняет заметки (`phoneVerifyId` и др.) и отдаёт `phone-call-form.ftl`:
номер для звонка, маскированный телефон пользователя, отсчёт TTL и JS-автосабмит
пустой формы на `${url.loginAction}` каждые 4 секунды.

Пользователь звонит на показанный номер со своего телефона и сбрасывает после
первого гудка.

### Шаг 5. Вебхук Plusofon (Plusofon → сервис)
```
POST https://merch-vibe.ru:4443/webhook/plusofon
Body: {"phone": "...", "key": "<одноразовый ключ>"}
→ 200 {"success": true}
```

Внутри сервиса:
1. **IP allowlist** (`WEBHOOK_IPS=185.54.48.1`, берётся из `CF-Connecting-IP` /
   `X-Real-IP` / `X-Forwarded-For`) — иначе 403.
2. Тело парсится гибко (JSON или form-urlencoded; `key` ищется по именам
   `key`, `code`, `pin`, `hash`, `token`), сырое тело логируется.
3. `store.find_by_key(key)` — ключ одноразовый и уникальный (404, если неизвестен).
4. `store.mark_verified` — перевод в `verified` возможен **только** из `pending`
   (идемпотентность повторных доставок: дубликаты получают `{"duplicate": true}`).

### Шаг 6. Поллинг статуса (SPI → сервис, каждые 4 с)
Каждый автосабмит формы — новый запрос к Keycloak, SPI в `action()` видит заметку
`phoneVerifyId` и опрашивает сервис:

```
GET {verificationServiceUrl}/api/verify/{verification_id}/status
Headers: X-Api-Key: {serviceApiKey}
→ 200 {"verification_id": "...", "status": "pending|verified|expired",
       "phone_masked": "79*******67", "ttl_left": 123}
```

Статус вычисляется с ленивым истечением: `pending` + `now > expires_at` →
`expired` (TTL 180 с). HTTP 404 от сервиса трактуется SPI как `expired`.

### Шаг 7. Успех: пользователь и завершение (внутри Keycloak)
При `status=verified` (`PhoneCallAuthenticator.succeed`):
1. Поиск пользователя: `users().searchForUserByUserAttributeStream(realm, "phoneNumber", phone)`.
2. Не найден и `userMode=create` → `users().addUser(realm, phone)`, `enabled=true`,
   атрибут `phoneNumber=phone` (при гонке/дубликате имени — повторный поиск).
   Не найден и `userMode=require` → ошибка на форме.
3. Пользователь отключён → ошибка на форме.
4. Иначе `ctx.setUser(user); ctx.success()` — Keycloak завершает flow: redirect
   с кодом авторизации, клиент обменивает его на токены (стандартный OIDC).

### Шаг 8. Неуспех
- `expired` → заметки сбрасываются, снова форма телефона с сообщением
  «Время ожидания звонка истекло».
- Сервис недоступен / ошибка HTTP → заметки сбрасываются, форма с сообщением.
- Повторный вход тем же номером: пользователь уже существует, дубль не создаётся.

---

## 4. Как устроен Java-код (keycloak/src)

Три класса + два шаблона:

### `PhoneCallAuthenticatorFactory`
- `getId() = "phone-call-auth"` — так аутентификатор виден в админке
  («Phone Call Verification (Plusofon)»).
- Синглтон-инстанс (`create()` возвращает `SINGLETON`) — аутентификатор stateless,
  всё состояние в auth notes.
- `getConfigProperties()` объявляет три параметра конфигурации:
  `verificationServiceUrl`, `serviceApiKey`, `userMode` (create|require).
- `getRequirementChoices()`: REQUIRED / ALTERNATIVE / DISABLED (без CONDITIONAL —
  см. ограничения KC 26 выше).
- Регистрируется через
  `META-INF/services/org.keycloak.authentication.AuthenticatorFactory`.

### `PhoneCallAuthenticator` (вся логика)
- `requiresUser() = false` — вход «с нуля», без предварительно аутентифицированного
  пользователя; `configuredFor()`/`setRequiredActions()` пустые (не required action).
- `authenticate()` и `action()` ведут в один `process(ctx)`, который работает
  как конечный автомат по трём признакам запроса:
  1. **Есть заметка `phoneVerifyId`** → фаза проверки статуса (шаг 6): `client.status(id)`;
     `verified` → `succeed()`, `expired` → `clearNotes()` + форма телефона с ошибкой,
     `pending` → снова страница ожидания (перерисовка с актуальным `ttlLeft`).
  2. **Нет заметок, но в POST есть поле `phone`** → фаза старта (шаги 1–4):
     нормализация → `client.start(phone, sessionRef)` → сохранить 5 заметок →
     страница ожидания.
  3. **Ни того, ни другого** → фаза 1: просто отрисовать форму ввода телефона.
- `succeed()` — поиск/создание пользователя по атрибуту `phoneNumber` и
  `ctx.setUser(); ctx.success()` (шаг 7). Гонка при создании обрабатывается
  повторным поиском.
- `challengePhoneForm` / `challengeCallPage` — рендер FreeMarker-шаблонов через
  `ctx.form()`; в шаблон ожидания передаются `callNumber` (отформатирован
  `formatCallNumber` → `+7 (939) 555-50-28`), `phoneMasked`, `ttlLeft`.
- `clearNotes()` — сброс всех пяти заметок (начать заново).
- Утилиты: `normalizePhone` (8…/+7…/10 цифр → 7…), `maskPhone` (`79*******67`),
  `formatCallNumber`, `ttlLeft` (из заметок `phoneVerifyStarted`/`phoneVerifyTtl`).

### `VerificationClient`
- Тонкий HTTP-клиент на `java.net.http.HttpClient` поверх двух эндпоинтов:
  `start(phone, sessionRef)` → `POST /api/verify/start`,
  `status(id)` → `GET /api/verify/{id}/status`.
- Заголовок `X-Api-Key` на оба запроса; таймауты 10 с (start) / 4 с (status);
  `404` от status трактуется как `expired`.
- **Важно**: клиент принудительно `HTTP/1.1` — без этого h2c-upgrade ломает body
  у uvicorn/FastAPI; `followRedirects=NEVER`.
- Ответы парсятся в record'ы `Start(verificationId, callNumber, ttl)` и
  `Status(verificationId, status, ttlLeft)`.

### Шаблоны (`theme-resources/templates/`)
- `phone-phone-form.ftl` — форма ввода номера, POST на `${url.loginAction}`.
- `phone-call-form.ftl` — страница ожидания: номер для звонка, обратный отсчёт
  TTL и два `setInterval` — тикание секунд и автосабмит пустой формы каждые 4 с
  (поллинг реализован именно перезагрузкой страницы, а не fetch'ем — так
  состояние живёт в Keycloak, а не в JS).

---

## 5. Защита цепочки (сводно)

- `X-Api-Key` на `/api/verify/*` — запросы без ключа (т.е. не SPI/не фронт) отклоняются.
- Rate-limit `start`: 5 запросов / 10 мин на номер (429) — защита от накрутки звонков.
- Вебхук: IP allowlist `185.54.48.1` (Plusofon), одноразовый `key` (уникальный в БД),
  перевод в `verified` только из `pending` (идемпотентность), TTL 180 c.
- Токен аккаунта Flash Call ≠ токен ЛК; `hook_url` подставляется сервисом в каждый
  `call-to-auth` из `PUBLIC_BASE_URL`.
- HTTPS: web- и keycloak-контейнеры терминируют TLS сами (копия сертификата
  системного nginx в `certs/`).

Подробный разбор рисков (спуфинг caller ID и т.п.) — `CONCEPT.md`, §5.

---

## 6. Замечание про standalone-фронтенд

`web/` (React) — независимый тестовый вход **мимо Keycloak**: те же два вызова
`POST /api/verify/start` и `GET /api/verify/{id}/status` (поллинг каждые 2 с,
`api.js`), в mock-режиме — кнопка «Симулировать звонок» → `POST /api/dev/simulate`
(доступен только при `MOCK=1`, эквивалентен вебхуку). Он полезен для отладки
сервиса верификации изолированно; в боевой цепочке входа участвует только SPI.
