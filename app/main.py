import ipaddress
import logging
import re
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config import CORS_ORIGINS, WEBHOOK_IPS, settings
from kcsync import sync_user_safe
from plusofon import PlusofonClient, PlusofonError
from schemas import (
    SimulateRequest,
    StartRequest,
    StartResponse,
    StatusResponse,
    WebhookBody,
)
from store import RateLimiter, Store, new_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("flashcall")

store = Store(settings.DB_PATH)
plusofon = PlusofonClient()
rate_limiter = RateLimiter(settings.RATE_LIMIT_N, settings.RATE_LIMIT_WINDOW)

app = FastAPI(title="Flash Call Verification Service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """SPI/фронтенд обязаны предъявлять X-Api-Key, если он сконфигурирован."""
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    # российские форматы: 8XXXXXXXXXX -> 7XXXXXXXXXX, 10 цифр -> 7XXXXXXXXXX
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    if not 10 <= len(digits) <= 15:
        raise HTTPException(status_code=422, detail="invalid phone number")
    return digits


def mask_phone(phone: str) -> str:
    if len(phone) < 4:
        return "***"
    return phone[:2] + "*" * (len(phone) - 4) + phone[-2:]


def format_call_number(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    if len(digits) == 11 and digits.startswith("8"):
        return f"{digits[0]} {digits[1:4]} {digits[4:7]} {digits[7:9]} {digits[9:11]}"
    return number


def client_ip(request: Request) -> str:
    """Реальный IP клиента с учётом прокси/туннеля (cloudflared/nginx)."""
    for header in ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else ""


def ip_allowed(request: Request) -> bool:
    if not WEBHOOK_IPS:  # проверка отключена
        return True
    ip = client_ip(request)
    for allowed in WEBHOOK_IPS:
        try:
            if ipaddress.ip_address(ip) in ipaddress.ip_network(allowed, strict=False):
                return True
        except ValueError:
            continue
    log.warning("webhook rejected: ip %s not in allowlist", ip)
    return False


@app.get("/api/health")
async def health():
    return {"status": "ok", "mock": settings.MOCK}


@app.post("/api/verify/start", response_model=StartResponse)
async def start_verification(req: StartRequest, _: None = Depends(require_api_key)):
    phone = normalize_phone(req.phone)
    try:
        rate_limiter.check(phone)
    except PermissionError as e:
        raise HTTPException(status_code=429, detail=str(e))

    if settings.MOCK:
        key, call_number = new_key(mock=True), settings.MOCK_CALL_NUMBER
    else:
        try:
            key, call_number = await plusofon.call_to_auth(phone)
        except PlusofonError as e:
            log.error("call-to-auth failed: %s", e)
            raise HTTPException(status_code=502, detail=str(e))

    v = store.create(
        key=key, phone=phone, session_ref=req.session_ref,
        call_number=call_number, mock=settings.MOCK, ttl=settings.CALL_TTL,
    )
    log.info("verification started: id=%s phone=%s mock=%s", v.id, mask_phone(phone), v.mock)
    return StartResponse(
        verification_id=v.id,
        call_number=format_call_number(call_number),
        ttl=v.ttl,
        mock=bool(v.mock),
    )


@app.get("/api/verify/{vid}/status", response_model=StatusResponse)
async def verification_status(vid: str, _: None = Depends(require_api_key)):
    v = store.get(vid)
    if v is None:
        raise HTTPException(status_code=404, detail="verification not found")
    status = store.status_of(v)
    ttl_left = max(0, int(v.expires_at - time.time())) if status == "pending" else 0
    return StatusResponse(
        verification_id=v.id, status=status,
        phone_masked=mask_phone(v.phone), ttl_left=ttl_left,
    )


@app.post("/webhook/plusofon")
async def plusofon_webhook(request: Request):
    """Вебхук от Plusofon после звонка пользователя.

    По доке ожидаем JSON {phone, key}, но реальный формат может отличаться
    (например form-urlencoded) — поэтому принимаем всё, логируем сырое тело
    и извлекаем key по возможным именам полей.

    Отвечаем 200 быстро; повторные доставки того же key — идемпотентны.
    """
    if not ip_allowed(request):
        return JSONResponse({"success": False, "message": "ip not allowed"}, status_code=403)

    raw = await request.body()
    log.info("webhook raw body (%s): %s", request.headers.get("content-type", "?"),
             raw.decode("utf-8", "replace")[:2000])

    fields: dict = {}
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        try:
            import json
            parsed = json.loads(raw or b"{}")
            if isinstance(parsed, dict):
                fields = parsed
        except ValueError:
            log.warning("webhook body is not valid JSON despite content-type %s", ctype)
    elif "form" in ctype:
        import urllib.parse
        fields = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8", "replace")).items()}
    else:
        # неизвестный формат: пробуем JSON, затем form
        import json
        import urllib.parse
        try:
            parsed = json.loads(raw or b"{}")
            fields = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            fields = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8", "replace")).items()}

    key = next((fields[k] for k in ("key", "code", "pin", "hash", "token") if k in fields), None)
    if not key:
        log.warning("webhook without key; fields=%s", list(fields))
        raise HTTPException(status_code=422, detail="webhook has no key field")

    v = store.find_by_key(key)
    if v is None:
        log.warning("webhook with unknown key=%s", key)
        raise HTTPException(status_code=404, detail="verification key not found")

    if store.status_of(v) != "pending":
        # уже verified/expired — подтверждаем получение, ничего не меняем
        return {"success": True, "duplicate": True}

    store.mark_verified(v.id)
    log.info("verification completed: id=%s phone=%s", v.id, mask_phone(v.phone))
    await sync_user_safe(v.phone)
    return {"success": True}


@app.post("/api/dev/simulate")
async def simulate_call(req: SimulateRequest, _: None = Depends(require_api_key)):
    """[только MOCK] Имитация звонка пользователя: эквивалент входящего вебхука."""
    if not settings.MOCK:
        raise HTTPException(status_code=403, detail="simulate is only available in MOCK mode")

    if req.verification_id:
        v = store.get(req.verification_id)
    elif req.phone:
        v = store.latest_pending_by_phone(normalize_phone(req.phone))
    else:
        raise HTTPException(status_code=422, detail="verification_id or phone required")

    if v is None:
        raise HTTPException(status_code=404, detail="verification not found")

    status = store.status_of(v)
    if status != "pending":
        raise HTTPException(status_code=409, detail=f"verification is {status}")

    store.mark_verified(v.id)
    log.info("simulated call: id=%s phone=%s", v.id, mask_phone(v.phone))
    await sync_user_safe(v.phone)
    return {"success": True, "verification_id": v.id, "status": "verified"}


# Отдаём собранный фронтенд (web/dist), если он есть — единый origin для теста.
_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="web")
