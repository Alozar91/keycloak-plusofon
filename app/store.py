import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass

STATUS_PENDING = "pending"
STATUS_VERIFIED = "verified"
STATUS_EXPIRED = "expired"


@dataclass
class Verification:
    id: str
    key: str
    phone: str
    session_ref: str | None
    call_number: str
    status: str
    mock: bool
    ttl: int
    created_at: float
    expires_at: float
    verified_at: float | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS verifications (
    id            TEXT PRIMARY KEY,
    key           TEXT UNIQUE NOT NULL,
    phone         TEXT NOT NULL,
    session_ref   TEXT,
    call_number   TEXT NOT NULL,
    status        TEXT NOT NULL,
    mock          INTEGER NOT NULL DEFAULT 0,
    ttl           INTEGER NOT NULL,
    created_at    REAL NOT NULL,
    expires_at    REAL NOT NULL,
    verified_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_verifications_key ON verifications (key);
"""


class Store:
    """Простое SQLite-хранилище верификаций (для тестового стенда этого достаточно)."""

    def __init__(self, path: str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def create(self, *, key: str, phone: str, session_ref: str | None,
               call_number: str, mock: bool, ttl: int) -> Verification:
        now = time.time()
        v = Verification(
            id=uuid.uuid4().hex,
            key=key,
            phone=phone,
            session_ref=session_ref,
            call_number=call_number,
            status=STATUS_PENDING,
            mock=int(mock),
            ttl=ttl,
            created_at=now,
            expires_at=now + ttl,
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO verifications (id, key, phone, session_ref, call_number,"
                " status, mock, ttl, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (v.id, v.key, v.phone, v.session_ref, v.call_number, v.status,
                 v.mock, v.ttl, v.created_at, v.expires_at),
            )
            self._conn.commit()
        return v

    def get(self, vid: str) -> Verification | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM verifications WHERE id = ?", (vid,)
            ).fetchone()
        return self._to_v(row)

    def find_by_key(self, key: str) -> Verification | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM verifications WHERE key = ?", (key,)
            ).fetchone()
        return self._to_v(row)

    def latest_pending_by_phone(self, phone: str) -> Verification | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM verifications WHERE phone = ? AND status = ?"
                " ORDER BY created_at DESC LIMIT 1",
                (phone, STATUS_PENDING),
            ).fetchone()
        return self._to_v(row)

    def status_of(self, v: Verification) -> str:
        """Текущий статус с ленивым истечением TTL."""
        if v.status == STATUS_PENDING and time.time() > v.expires_at:
            self._set_status(v.id, STATUS_EXPIRED)
            return STATUS_EXPIRED
        return v.status

    def mark_verified(self, vid: str) -> bool:
        """Одноразовость: перевести в verified можно только pending-запись."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE verifications SET status = ?, verified_at = ?"
                " WHERE id = ? AND status = ?",
                (STATUS_VERIFIED, time.time(), vid, STATUS_PENDING),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def _set_status(self, vid: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE verifications SET status = ? WHERE id = ?", (status, vid)
            )
            self._conn.commit()

    @staticmethod
    def _to_v(row: sqlite3.Row | None) -> Verification | None:
        if row is None:
            return None
        return Verification(
            id=row["id"], key=row["key"], phone=row["phone"],
            session_ref=row["session_ref"], call_number=row["call_number"],
            status=row["status"], mock=row["mock"], ttl=row["ttl"],
            created_at=row["created_at"], expires_at=row["expires_at"],
            verified_at=row["verified_at"],
        )


class RateLimiter:
    """Простой in-memory лимитер: не более n вызовов на ключ за окно."""

    def __init__(self, n: int, window: int):
        self._n = n
        self._window = window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self._window]
            if len(hits) >= self._n:
                raise PermissionError(f"too many requests for {key}")
            hits.append(now)
            self._hits[key] = hits


def new_key(mock: bool) -> str:
    """Ключ проверки. В mock-режиме помечаем префиксом для наглядности."""
    token = secrets.token_urlsafe(18)
    return f"mock-{token}" if mock else token
