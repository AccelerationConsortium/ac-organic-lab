"""SQLite storage for the v1 email-code auth flow.

Three tables — the allow-list (``users``), one-time login codes
(``login_codes``), and opaque ``sessions``. Process-wide single connection with
WAL + a write lock (see AUTH_SERVICE_DESIGN.md: SQLite serializes writers
DB-wide). Methods are synchronous and fast; the FastAPI routes call them via
``asyncio.to_thread`` so the event loop never blocks.

Secrets-at-rest: login codes and session tokens are stored **hashed**
(SHA-256); the plaintext only ever exists transiently to email / set the cookie.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Human account roles (authN gives one of these). The *device* role is derived
# from this plus is_automation by the resolver seam in authz.py — keep these
# two in sync only through that function.
VALID_ROLES = ("user", "admin")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    email              TEXT PRIMARY KEY,
    role               TEXT NOT NULL DEFAULT 'user',
    status             TEXT NOT NULL DEFAULT 'active',
    is_automation INTEGER NOT NULL DEFAULT 0,
    created_at         REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL,
    code_hash  TEXT NOT NULL,
    expires_at REAL NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    used       INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_codes_email ON login_codes(email);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
-- Machine principals (robot/platform service accounts) authenticate by key, not
-- email code. A key belongs to a users row with is_automation=1.
CREATE TABLE IF NOT EXISTS api_keys (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL,
    key_hash   TEXT NOT NULL UNIQUE,
    label      TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    expires_at REAL,
    revoked    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_keys_email ON api_keys(email);
"""


def _now() -> float:
    return time.time()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def norm_email(email: str) -> str:
    return email.strip().lower()


@dataclass(frozen=True)
class User:
    email: str
    role: str
    status: str
    is_automation: bool = False


@dataclass(frozen=True)
class ApiKeyInfo:
    """A machine principal's key (metadata only — the secret is never stored)."""

    id: int
    email: str
    label: str
    created_at: float
    expires_at: Optional[float]
    revoked: bool


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        row["email"], row["role"], row["status"], bool(row["is_automation"])
    )


class Db:
    """Thread-safe SQLite store (single connection + write lock)."""

    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Additive migrations for DBs created before a column existed
        (``CREATE TABLE IF NOT EXISTS`` never alters an existing table)."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(users)").fetchall()}
        if "is_automation" not in cols:
            if "is_service_account" in cols:
                # Historical column name (pre-rename). Rename in place — preserves
                # the stored values; no automation accounts existed at rename time.
                self._conn.execute(
                    "ALTER TABLE users RENAME COLUMN is_service_account TO is_automation"
                )
            else:
                self._conn.execute(
                    "ALTER TABLE users ADD COLUMN is_automation INTEGER NOT NULL DEFAULT 0"
                )

    # ---- users (the allow-list) -------------------------------------------

    def upsert_user(
        self,
        email: str,
        role: str = "user",
        status: str = "active",
        is_automation: bool = False,
    ) -> User:
        if role not in VALID_ROLES:
            raise ValueError(f"role must be one of {VALID_ROLES}")
        email = norm_email(email)
        with self._lock:
            self._conn.execute(
                """INSERT INTO users (email, role, status, is_automation, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(email) DO UPDATE SET
                       role=excluded.role, status=excluded.status,
                       is_automation=excluded.is_automation""",
                (email, role, status, int(is_automation), _now()),
            )
            self._conn.commit()
        return User(email, role, status, bool(is_automation))

    def get_user(self, email: str) -> Optional[User]:
        email = norm_email(email)
        with self._lock:
            row = self._conn.execute(
                "SELECT email, role, status, is_automation FROM users WHERE email=?", (email,)
            ).fetchone()
        return _row_to_user(row) if row else None

    def list_users(self, *, active_only: bool = False) -> list[User]:
        sql = "SELECT email, role, status, is_automation FROM users"
        if active_only:
            sql += " WHERE status='active'"
        sql += " ORDER BY email"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [_row_to_user(r) for r in rows]

    def set_status(self, email: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET status=? WHERE email=?", (status, norm_email(email))
            )
            self._conn.commit()

    def delete_user(self, email: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM users WHERE email=?", (norm_email(email),))
            self._conn.commit()

    # ---- one-time login codes ---------------------------------------------

    def create_login_code(self, email: str, code: str, ttl_s: int) -> None:
        """Store a new code (hashed); invalidate any prior unused codes for the
        email so only the latest is live."""
        email = norm_email(email)
        now = _now()
        with self._lock:
            self._conn.execute(
                "UPDATE login_codes SET used=1 WHERE email=? AND used=0", (email,)
            )
            self._conn.execute(
                "INSERT INTO login_codes (email, code_hash, expires_at, attempts, used, created_at)"
                " VALUES (?, ?, ?, 0, 0, ?)",
                (email, _hash(code), now + ttl_s, now),
            )
            self._conn.commit()

    def verify_login_code(self, email: str, code: str, max_attempts: int) -> bool:
        """Check ``code`` against the latest live code for ``email``. Burns the
        code on success, on expiry, or once attempts are exhausted; otherwise
        increments the attempt counter. Constant-time hash comparison."""
        email = norm_email(email)
        with self._lock:
            row = self._conn.execute(
                "SELECT id, code_hash, expires_at, attempts, used FROM login_codes"
                " WHERE email=? ORDER BY id DESC LIMIT 1",
                (email,),
            ).fetchone()
            if row is None or row["used"]:
                return False
            if _now() > row["expires_at"] or row["attempts"] >= max_attempts:
                self._conn.execute("UPDATE login_codes SET used=1 WHERE id=?", (row["id"],))
                self._conn.commit()
                return False
            if secrets.compare_digest(row["code_hash"], _hash(code)):
                self._conn.execute("UPDATE login_codes SET used=1 WHERE id=?", (row["id"],))
                self._conn.commit()
                return True
            self._conn.execute(
                "UPDATE login_codes SET attempts=attempts+1 WHERE id=?", (row["id"],)
            )
            self._conn.commit()
            return False

    # ---- opaque sessions ---------------------------------------------------

    def create_session(self, email: str, ttl_s: int) -> str:
        """Create a session; return the plaintext token (stored hashed)."""
        token = secrets.token_urlsafe(32)
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (token_hash, email, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_hash(token), norm_email(email), now, now + ttl_s),
            )
            self._conn.commit()
        return token

    def session_email(self, token: str) -> Optional[str]:
        """Return the email for a live (unexpired) session token, else None."""
        if not token:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT email, expires_at FROM sessions WHERE token_hash=?", (_hash(token),)
            ).fetchone()
        if row is None or _now() > row["expires_at"]:
            return None
        return row["email"]

    def revoke_session(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token_hash=?", (_hash(token),))
            self._conn.commit()

    # ---- API keys (machine principals) ------------------------------------

    def create_api_key(self, email: str, label: str = "", ttl_s: Optional[int] = None) -> str:
        """Issue a key for a (service-account) user; return the plaintext token
        once — only its hash is stored. ``ttl_s=None`` → no expiry."""
        email = norm_email(email)
        token = "ak_" + secrets.token_urlsafe(32)
        now = _now()
        expires_at = (now + ttl_s) if ttl_s else None
        with self._lock:
            self._conn.execute(
                "INSERT INTO api_keys (email, key_hash, label, created_at, expires_at, revoked)"
                " VALUES (?, ?, ?, ?, ?, 0)",
                (email, _hash(token), label, now, expires_at),
            )
            self._conn.commit()
        return token

    def verify_api_key(self, token: str) -> Optional[User]:
        """Return the principal :class:`User` for a live (un-revoked, unexpired)
        key, else ``None``. The caller still checks ``status == 'active'``."""
        if not token:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT email, expires_at, revoked FROM api_keys WHERE key_hash=?", (_hash(token),)
            ).fetchone()
        if row is None or row["revoked"]:
            return None
        if row["expires_at"] is not None and _now() > row["expires_at"]:
            return None
        return self.get_user(row["email"])

    def list_api_keys(self, email: str) -> list[ApiKeyInfo]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, email, label, created_at, expires_at, revoked FROM api_keys"
                " WHERE email=? ORDER BY id",
                (norm_email(email),),
            ).fetchall()
        return [
            ApiKeyInfo(r["id"], r["email"], r["label"], r["created_at"], r["expires_at"], bool(r["revoked"]))
            for r in rows
        ]

    def revoke_api_key(self, key_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (key_id,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
