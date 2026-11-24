"""Dev-перехватчик писем Keycloak (вместо SMTP).

Keycloak настраивается на SMTP-хост `app` (порт 1025, без TLS/auth).
Здесь поднимается мини-AIOSMTP-сервер: письма не отправляются, а ссылки
подтверждения складываются в dev-outbox (SQLite), откуда их забирает
фронтенд через GET /api/dev/outbox.

ТОЛЬКО для проверки идеи multi-входа. Не для прода.
"""
import asyncio
import logging
import re
from email import message_from_bytes
from email.utils import parseaddr

from store import Outbox

log = logging.getLogger("devmail")

URL_RE = re.compile(r"https?://[^\s<>\"']+")


def extract_text(raw: bytes) -> tuple[str, str, str | None]:
    """(from, to, первая ссылка) из сырого письма."""
    msg = message_from_bytes(raw)
    frm = msg.get("From", "")
    to = parseaddr(msg.get("To", ""))[1]
    link = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                m = URL_RE.search(payload.decode("utf-8", "replace"))
                if m:
                    link = m.group(0)
                break
    else:
        payload = msg.get_payload(decode=True) or b""
        m = URL_RE.search(payload.decode("utf-8", "replace"))
        if m:
            link = m.group(0)
    return frm, to, link


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                         outbox: Outbox) -> None:
    peer = writer.get_extra_info("peername")
    log.info("connection from %s", peer)

    async def send(line: str) -> None:
        writer.write((line + "\r\n").encode())
        await writer.drain()

    await send("220 devmail ready")
    data_mode = False
    buffer = b""
    mail_from = ""
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            if data_mode:
                if line.strip() == b".":
                    frm, to, link = extract_text(buffer)
                    if to:
                        outbox.put(to_addr=to, subject=f"letter from {frm}",
                                   link=link, body=buffer.decode("utf-8", "replace")[:4000])
                        log.info("captured mail to=%s link=%s", to, link)
                    else:
                        log.warning("captured mail without To: from=%s", frm)
                    buffer = b""
                    data_mode = False
                    await send("250 OK queued (dev)")
                else:
                    buffer += line
                continue

            cmd = line.decode("utf-8", "replace").strip()
            upper = cmd.upper()
            if upper.startswith("EHLO") or upper.startswith("HELO"):
                # multi-line EHLO: последняя строка без дефиса
                await send("250-devmail")
                await send("250 OK")
            elif upper.startswith("MAIL FROM"):
                mail_from = cmd
                await send("250 OK")
            elif upper.startswith("RCPT TO"):
                await send("250 OK")
            elif upper == "DATA":
                await send("354 end data with <CR><LF>.<CR><LF>")
                data_mode = True
            elif upper == "QUIT":
                await send("221 bye")
                break
            elif upper == "RSET":
                buffer = b""
                await send("250 OK")
            elif upper == "NOOP":
                await send("250 OK")
            else:
                await send("250 OK")  # на всё отвечаем OK — это заглушка
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def start_dev_mail_server(outbox: Outbox, host: str = "0.0.0.0", port: int = 1025) -> None:
    async def handler(reader, writer):
        await _handle_client(reader, writer, outbox)

    server = await asyncio.start_server(handler, host, port)
    log.info("dev mail server listening on %s:%s", host, port)
    asyncio.create_task(server.serve_forever())
