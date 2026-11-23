#!/usr/bin/env bash
# Настройка realm "plusofon-test" с flow "phone-browser":
#   [Cookie (CONDITIONAL)] -> [Phone Call Auth (REQUIRED, конфиг +)
# Аутентификатор phone-call-auth из phone-verify-spi.jar.
# Идемпотентно: удаляет и пересоздаёт realm.
#
# Проверено на Keycloak 26.3:
#   - execution добавляется: POST /authentication/flows/{alias}/executions/execution {"provider": ...}
#   - requirement меняется:  PUT  /authentication/flows/{alias}/executions (representation из GET)
#   - конфиг execution:      POST /authentication/executions/{id}/config {"alias","config"}
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="https://merch-vibe.ru:8443"
REALM="plusofon-test"
FLOW="phone-browser"
set -a; . ./.env; set +a   # API_KEY, KEYCLOAK_ADMIN/PASSWORD

python3 - "$BASE" "$REALM" "$FLOW" "$API_KEY" \
          "${KEYCLOAK_ADMIN:-admin}" "${KEYCLOAK_ADMIN_PASSWORD:-admin}" <<'PYEOF'
import json, sys, time, urllib.request, urllib.error, urllib.parse

BASE, REALM, FLOW, API_KEY, ADMIN_USER, ADMIN_PASSWORD = sys.argv[1:7]

def req(url, method="GET", body=None, token=None, ok=(200, 201, 204)):
    r = urllib.request.Request(url, method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        resp = urllib.request.urlopen(r)
        code = resp.status
        raw = resp.read()
    except urllib.error.HTTPError as e:
        code = e.code
        raw = e.read()
    return code, (raw.decode() if raw else "")

# 1. ждём Keycloak и берём админ-токен
token = ""
for i in range(30):
    data = urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                   "username": ADMIN_USER, "password": ADMIN_PASSWORD}).encode()
    r = urllib.request.Request(BASE + "/realms/master/protocol/openid-connect/token", data=data)
    try:
        token = json.loads(urllib.request.urlopen(r).read())["access_token"]
        break
    except Exception:
        print(f"  ...оживание Keycloak ({i+1}/30)"); time.sleep(5)
if not token:
    sys.exit("FAIL: админ-токен не получен")
print("== 1. админ-токен получен ==")

A = BASE + "/admin/realms"

# 2. пересоздаём realm
code, _ = req(f"{A}/{REALM}", "DELETE", token=token)
code, body = req(A, "POST", {"realm": REALM, "enabled": True, "registrationAllowed": False,
                             "loginWithEmailAllowed": False, "resetPasswordAllowed": False,
                             "sslRequired": "external"}, token=token)
print(f"== 2. create realm: {code} ==")
if code != 201: sys.exit(body)

# 3. flow
code, body = req(f"{A}/{REALM}/authentication/flows", "POST",
                 {"alias": FLOW, "topLevel": True, "providerId": "basic-flow",
                  "builtIn": False, "description": "Phone-first login (reverse Flash Call)"},
                 token=token)
print(f"== 3. create flow: {code} ==")
if code != 201: sys.exit(body)

# 4. executions: cookie (ALTERNATIVE) + phone-call-auth (ALTERNATIVE) + конфиг
# ВАЖНО: в KC 26 аутентификатор с requirement=CONDITIONAL после ATTEMPTED ломает флоу
# (AuthenticationFlowException: UNKNOWN_USER). Стандарт browser-flow: cookie ALTERNATIVE.
# REQUIRED рядом с ALTERNATIVE тоже нельзя — остальные игнорируются.
ids = {}
for provider in ("auth-cookie", "phone-call-auth"):
    # Location: .../executions/{id}
    code, body = req(f"{A}/{REALM}/authentication/flows/{FLOW}/executions/execution", "POST",
                     {"provider": provider}, token=token)
    if code != 201:
        sys.exit(f"add {provider}: {code} {body}")
    lst = json.loads(req(f"{A}/{REALM}/authentication/flows/{FLOW}/executions", token=token)[1])
    ids[provider] = next(e["id"] for e in lst if e.get("providerId") == provider)
print(f"== 4. executions: {ids} ==")

ex = json.loads(req(f"{A}/{REALM}/authentication/flows/{FLOW}/executions", token=token)[1])
for rep in ex:
    if rep["id"] in ids.values():
        rep["requirement"] = "ALTERNATIVE"
        code, body = req(f"{A}/{REALM}/authentication/flows/{FLOW}/executions", "PUT", rep, token=token)
        if code != 204: sys.exit(f"requirement PUT: {code} {body}")

code, body = req(f"{A}/{REALM}/authentication/executions/{ids['phone-call-auth']}/config", "POST",
                 {"alias": "plusofon", "config": {
                     "verificationServiceUrl": "http://app:8000",
                     "serviceApiKey": API_KEY,
                     "userMode": "create"}}, token=token)
print(f"   config: {code}")
if code != 201: sys.exit(body)

# 4b. user profile: разрешаем неуправляемые атрибуты (иначе атрибут phoneNumber
# не сохранится на пользователе — профиль KC 26 их вырезает)
st, upc = req(f"{A}/{REALM}/users/profile", token=token)
upc = json.loads(upc); upc["unmanagedAttributePolicy"] = "ADMIN_EDIT"
code, _ = req(f"{A}/{REALM}/users/profile", "PUT", upc, token=token)
print(f"== 4b. unmanagedAttributePolicy=ADMIN_EDIT: {code} ==")

# 5. привязываем как browser flow
code, realm = req(f"{A}/{REALM}", token=token)
realm = json.loads(realm); realm["browserFlow"] = FLOW
code, _ = req(f"{A}/{REALM}", "PUT", realm, token=token)
print(f"== 5. browserFlow={FLOW}: {code} ==")

# 6. итог
ex = json.loads(req(f"{A}/{REALM}/authentication/flows/{FLOW}/executions", token=token)[1])
print("== итог ==")
for e in ex:
    print(f"   {e.get('displayName')} → {e.get('requirement')}")
print(f"   Тест входа: {BASE}/realms/{REALM}/account")
print(f"   Админка:    {BASE}/admin/master/console/ (realm {REALM})")
PYEOF
