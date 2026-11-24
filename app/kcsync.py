"""Синхронизация подтверждённого номера в Keycloak.

После успешной верификации (вебхук Plusofon / simulate) сервис сам создаёт
в realm пользователя с атрибутом phoneNumber — тот же результат, что давал бы
SPI при входе через Keycloak. Идемпотентно: сначала поиск по атрибуту,
создание только если пользователя нет.
"""
import logging

import httpx

from config import settings

log = logging.getLogger("kcsync")


class KeycloakSyncError(RuntimeError):
    pass


async def _admin_token(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{settings.KC_BASE_URL}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": settings.KC_ADMIN_USER,
            "password": settings.KC_ADMIN_PASSWORD,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code != 200:
        raise KeycloakSyncError(f"admin token HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()["access_token"]


async def sync_user(phone: str) -> str:
    """Создать пользователя с phoneNumber=phone, если его ещё нет.

    :return: 'created' | 'exists'
    """
    async with httpx.AsyncClient(timeout=10) as client:
        token = await _admin_token(client)
        auth = {"Authorization": f"Bearer {token}"}
        base = f"{settings.KC_BASE_URL}/admin/realms/{settings.KC_REALM}"

        # ищем по атрибуту phoneNumber (как SPI)
        resp = await client.get(
            f"{base}/users", params={"q": f"phoneNumber:{phone}", "exact": "true", "max": 1},
            headers=auth,
        )
        if resp.status_code == 200 and resp.json():
            return "exists"

        resp = await client.post(
            f"{base}/users",
            headers=auth,
            json={
                "username": phone,
                "enabled": True,
                "attributes": {"phoneNumber": [phone]},
            },
        )
        if resp.status_code == 201:
            return "created"
        # 409: username занят (например, гонка) — считаем, что пользователь есть
        if resp.status_code == 409:
            return "exists"
        raise KeycloakSyncError(f"create user HTTP {resp.status_code}: {resp.text[:200]}")


async def sync_user_safe(phone: str) -> None:
    """Синхронизация, которая не ломает основной поток: только логирует ошибки."""
    if not settings.KC_SYNC:
        return
    try:
        result = await sync_user(phone)
        log.info("keycloak sync: phone=%s -> %s", phone[:2] + "***" + phone[-2:], result)
    except Exception as e:
        log.error("keycloak sync failed: phone=%s error=%s", phone[:2] + "***" + phone[-2:], e)
