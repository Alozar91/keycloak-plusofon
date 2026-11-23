import httpx

from config import settings

CLIENT_HEADER = "10553"  # постоянное значение заголовка Client для Plusofon API


class PlusofonError(RuntimeError):
    pass


class PlusofonClient:
    """Клиент метода «Обратный Flash Call» (call-to-auth) Plusofon API.

    Дока: https://help.plusofon.ru/api/v1/flash-call
    Для call-to-auth используется access_token аккаунта Flash Call
    (не основной токен личного кабинета!).
    """

    async def call_to_auth(self, phone: str) -> tuple[str, str]:
        """Запросить CallToAuth-номер для проверочного звонка пользователя.

        :param phone: проверяемый номер пользователя (нормализованный)
        :return: (key, call_number) — ключ проверки и номер, который показать пользователю
        """
        if not settings.PLUSOFON_FC_ACCOUNT_TOKEN:
            raise PlusofonError(
                "PLUSOFON_FC_ACCOUNT_TOKEN is not set — "
                "создайте аккаунт Flash Call (POST /api/v1/flash-call) и укажите его access_token"
            )

        hook_url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/webhook/plusofon"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Client": CLIENT_HEADER,
            "Authorization": f"Bearer {settings.PLUSOFON_FC_ACCOUNT_TOKEN}",
        }
        payload = {"phone": phone, "hook_url": hook_url}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.PLUSOFON_API_BASE}/api/v1/flash-call/call-to-auth",
                headers=headers,
                json=payload,
            )

        if resp.status_code != 200:
            raise PlusofonError(f"Plusofon HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            data = resp.json()
        except ValueError as e:
            raise PlusofonError(f"Plusofon returned non-JSON: {resp.text[:300]}") from e

        if not data.get("success"):
            raise PlusofonError(f"Plusofon error: {data}")

        d = data.get("data") or {}
        key = d.get("key")
        call_number = d.get("phone")
        if not key or not call_number:
            raise PlusofonError(f"Unexpected Plusofon response: {data}")

        return str(key), str(call_number)
