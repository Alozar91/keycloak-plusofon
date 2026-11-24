"""Профиль пользователя для фронтенда: вход через Keycloak (PKCE), данные
профиля и управление способами входа.

Фронтенд — публичный OIDC-клиент `web-profile` (создаётся setup_keycloak.sh).
Авторизация: Authorization Code + PKCE в браузере, токены только в JS-памяти.
API-эндпоинты принимают Bearer access token и проверяют его локально через
JWKS realm'а (iss + exp + подпись).
"""
import base64
import hashlib
import json
import logging
import re
import secrets
import time

import httpx
import jwt  # PyJWT
from fastapi import APIRouter, Depends, Header, HTTPException
from jwt import PyJWKClient

from config import settings
from store import Store, STATUS_VERIFIED

log = logging.getLogger("profile")

# хранилище верификаций нужно для проверки привязки телефона
store: Store | None = None


def init_profile(store_ref: Store) -> None:
    global store
    store = store_ref

router = APIRouter(prefix="/api/profile", tags=["profile"])

_jwk_client: PyJWKClient | None = None


def _jwks() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(f"{settings.KC_BASE_URL}/realms/{settings.KC_REALM}/protocol/openid-connect/certs")
    return _jwk_client


def current_user(authorization: str = Header(default="")) -> dict:
    """Проверка Bearer-токена: подпись (JWKS), iss, exp. Возвращает claims."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:]
    try:
        key = _jwks().get_signing_key_from_jwt(token).key
        # aud у публичных клиентов KC часто отсутствует — проверяем azp и iss,
        # но отключаем aud-проверку PyJWT (verify_aud=False), т.к. аудитория
        # контролируем через azp == web-profile и iss realm'а
        claims = jwt.decode(
            token, key,
            algorithms=["RS256"],
            options={"verify_aud": False},
            issuer=f"{settings.KC_PUBLIC_BASE_URL}/realms/{settings.KC_REALM}",
        )
    except Exception as e:
        log.info("token rejected: %s", e)
        raise HTTPException(status_code=401, detail="invalid token")
    if claims.get("azp") != "web-profile":
        raise HTTPException(status_code=403, detail="wrong client")
    if "profile" not in claims.get("scope", ""):
        raise HTTPException(status_code=403, detail="profile scope required")
    return claims


# ---------------------------------------------------------------- KC admin API

async def _admin(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{settings.KC_BASE_URL}/realms/master/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "admin-cli",
              "username": settings.KC_ADMIN_USER, "password": settings.KC_ADMIN_PASSWORD},
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="keycloak admin token failed")
    return resp.json()["access_token"]


async def _kc(client: httpx.AsyncClient, method: str, path: str, token: str, **kw):
    resp = await client.request(
        method, f"{settings.KC_BASE_URL}/admin/realms/{settings.KC_REALM}{path}",
        headers={"Authorization": f"Bearer {token}"}, **kw,
    )
    return resp


# ------------------------------------------------------------------- endpoints

@router.get("")
async def get_profile(claims: dict = Depends(current_user)):
    """Профиль: username, email, телефон, способы входа (линк + креды)."""
    user_id = claims["sub"]
    async with httpx.AsyncClient(timeout=10) as client:
        token = await _admin(client)
        r = await _kc(client, "GET", f"/users/{user_id}", token)
        if r.status_code != 200:
            raise HTTPException(status_code=404, detail="user not found")
        u = r.json()
        r = await _kc(client, "GET", f"/users/{user_id}/federated-identity", token)
        links = r.json() if r.status_code == 200 else []
        creds = await _kc(client, "GET", f"/users/{user_id}/credentials", token)
        cred_list = creds.json() if creds.status_code == 200 else []

    phone = (u.get("attributes") or {}).get("phoneNumber")
    phone = phone[0] if isinstance(phone, list) and phone else phone
    return {
        "username": u.get("username"),
        "email": u.get("email"),
        "email_verified": bool(u.get("emailVerified")),
        "phone": phone,
        "created": u.get("createdTimestamp"),
        "logins": {
            "phone": bool(phone) or any(l.get("identityProvider") == "phone-call" for l in links),
            "password": any(c.get("type") == "password" for c in cred_list),
            "email": bool(u.get("email")),
        },
        "federated": [
            {"provider": l.get("identityProvider"), "external_id": l.get("userId")}
            for l in links if l.get("identityProvider") == "phone-call"
        ],
    }


@router.put("/username")
async def set_username(body: dict, claims: dict = Depends(current_user)):
    username = (body.get("username") or "").strip()
    if not username or not json.dumps(username, ensure_ascii=False):
        raise HTTPException(status_code=422, detail="username required")
    if len(username) < 3 or len(username) > 64:
        raise HTTPException(status_code=422, detail="username length 3..64")
    async with httpx.AsyncClient(timeout=10) as client:
        token = await _admin(client)
        r = await _kc(client, "GET", f"/users/{claims['sub']}", token)
        if r.status_code != 200:
            raise HTTPException(status_code=404, detail="user not found")
        u = r.json()
        u["username"] = username
        r = await _kc(client, "PUT", f"/users/{claims['sub']}", token, json=u)
        if r.status_code == 409:
            raise HTTPException(status_code=409, detail="username already taken")
        if r.status_code not in (200, 204):
            raise HTTPException(status_code=400, detail=r.text[:200])
    return {"ok": True}


@router.post("/email")
async def set_email(body: dict, claims: dict = Depends(current_user)):
    """Задать email и запустить верификацию (VERIFY_EMAIL required action).

    В dev-режиме письмо попадает в dev-outbox, ссылка видна на фронте.
    """
    email = (body.get("email") or "").strip().lower()
    if "@" not in email or len(email) > 254:
        raise HTTPException(status_code=422, detail="invalid email")
    user_id = claims["sub"]
    async with httpx.AsyncClient(timeout=10) as client:
        token = await _admin(client)
        # коллизия email?
        r = await _kc(client, "GET", f"/users?email={email}&exact=true", token)
        found = r.json() if r.status_code == 200 else []
        if any(x["id"] != user_id for x in found):
            raise HTTPException(status_code=409, detail="email already in use")
        r = await _kc(client, "GET", f"/users/{user_id}", token)
        u = r.json()
        u["email"] = email
        u["emailVerified"] = False
        r = await _kc(client, "PUT", f"/users/{user_id}", token, json=u)
        if r.status_code not in (200, 204):
            raise HTTPException(status_code=400, detail=r.text[:200])
        # запускаем verify-email (письмо уйдёт в dev-outbox через SMTP-заглушку)
        r = await _kc(client, "PUT", f"/users/{user_id}/send-verify-email", token,
                      params={"client_id": "web-profile",
                              "redirect_uri": f"{settings.KC_PUBLIC_FRONTEND}/profile"})
        if r.status_code not in (200, 204):
            log.warning("send-verify-email: %s %s", r.status_code, r.text[:200])
    return {"ok": True, "hint": "письмо с ссылкой — в dev-outbox на главной странице"}


@router.post("/phone")
async def bind_phone(body: dict, claims: dict = Depends(current_user)):
    """Привязка телефона к текущему пользователю.

    Требует свежей верификации звонком: verification_id + phone. Проверяем,
    что verification verified, номер совпадает и с телом, и со звонившим
    (харденинг вебхука уже отсеял спуфинг).
    """
    from store import STATUS_VERIFIED  # noqa: F401 — импорт для читаемости
    phone = (body.get("phone") or "").strip()
    vid = body.get("verification_id") or ""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if not re.fullmatch(r"7\d{10}", digits):
        raise HTTPException(status_code=422, detail="invalid phone")
    v = store.get(vid)
    if v is None or v.phone != digits or store.status_of(v) != STATUS_VERIFIED:
        raise HTTPException(status_code=400, detail="verification not confirmed for this phone")
    user_id = claims["sub"]
    async with httpx.AsyncClient(timeout=10) as client:
        token = await _admin(client)
        # номер уже у другого пользователя?
        r = await _kc(client, "GET", f"/users?q=phoneNumber:{digits}&exact=true", token)
        found = r.json() if r.status_code == 200 else []
        if any(x["id"] != user_id for x in found):
            raise HTTPException(status_code=409, detail="phone already linked to another user")
        # линк phone-call уже есть у другого пользователя?
        for x in found:
            pass
        r = await _kc(client, "GET", f"/users/{user_id}", token)
        u = r.json()
        attrs = u.get("attributes") or {}
        attrs["phoneNumber"] = [digits]
        u["attributes"] = attrs
        r = await _kc(client, "PUT", f"/users/{user_id}", token, json=u)
        if r.status_code not in (200, 204):
            raise HTTPException(status_code=400, detail=r.text[:200])
        # federated identity link (user id = номер, как при входе через IdP)
        r = await _kc(client, "POST", f"/users/{user_id}/federated-identity/phone-call", token,
                      json={"identityProvider": "phone-call", "userId": digits, "userName": digits})
        if r.status_code == 409:
            pass  # линк уже существует — ок
        elif r.status_code not in (200, 201, 204):
            raise HTTPException(status_code=400, detail=r.text[:200])
    return {"ok": True}


@router.delete("/phone")
async def unbind_phone(claims: dict = Depends(current_user)):
    """Отвязать телефон: убрать линк и атрибут."""
    user_id = claims["sub"]
    async with httpx.AsyncClient(timeout=10) as client:
        token = await _admin(client)
        r = await _kc(client, "GET", f"/users/{user_id}/federated-identity", token)
        links = r.json() if r.status_code == 200 else []
        for l in links:
            if l.get("identityProvider") == "phone-call":
                await _kc(client, "DELETE", f"/users/{user_id}/federated-identity/phone-call", token)
        r = await _kc(client, "GET", f"/users/{user_id}", token)
        u = r.json()
        attrs = u.get("attributes") or {}
        attrs.pop("phoneNumber", None)
        u["attributes"] = attrs
        await _kc(client, "PUT", f"/users/{user_id}", token, json=u)
    return {"ok": True}


@router.post("/password")
async def set_password(body: dict, claims: dict = Depends(current_user)):
    """Задать/сменить пароль (через admin API, от имени подтверждённого пользователя)."""
    password = body.get("password") or ""
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="password min 8 chars")
    user_id = claims["sub"]
    async with httpx.AsyncClient(timeout=10) as client:
        token = await _admin(client)
        r = await _kc(client, "PUT", f"/users/{user_id}/reset-password", token,
                      json={"type": "password", "value": password, "temporary": False})
        if r.status_code not in (200, 204):
            raise HTTPException(status_code=400, detail=r.text[:200])
    return {"ok": True}
