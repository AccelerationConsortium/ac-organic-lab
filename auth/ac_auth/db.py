"""SQLite storage for the v1 email-code auth flow.

Runtime-state tables — one-time login codes (``login_codes``), opaque
``sessions``, machine ``api_keys``, and the append-only ``auth_events`` audit
log (the durable login history; sessions/codes are prunable working state).
The legacy ``users`` table remains for the CLI export bootstrap only — the
allow-list lives in ``roster.yaml``. Process-wide single connection with
WAL + a write lock (see AUTH_DESIGN.md: SQLite serializes writers
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
from dataclasses import dataclass, field
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
    -- account-management metadata (all additive; see _migrate for live DBs)
    name               TEXT NOT NULL DEFAULT '',   -- display name
    lab_account        TEXT NOT NULL DEFAULT '',   -- group / PI affiliation
    notes              TEXT NOT NULL DEFAULT '',   -- free-form admin notes
    expires_at         REAL,                       -- account lapse; enforced at login
    last_login_at      REAL,                       -- stamped on each successful verify
    email_verified     INTEGER NOT NULL DEFAULT 0, -- set true on first code sign-in
    disabled_at        REAL,                       -- when status flipped to disabled
    disabled_reason    TEXT NOT NULL DEFAULT '',   -- why it was disabled
    created_at         REAL NOT NULL               -- "active since" (row creation)
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
-- Append-only audit log — the durable login history (AUTH_DESIGN: SQLite is
-- runtime state; this table is what makes sessions/login_codes safely prunable).
-- Never updated or deleted by the service; retention is an operator decision.
CREATE TABLE IF NOT EXISTS auth_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    email      TEXT NOT NULL DEFAULT '',
    event      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    ip         TEXT NOT NULL DEFAULT '',   -- Tailnet source identifies the machine
    user_agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_auth_events_ts ON auth_events(ts);
CREATE INDEX IF NOT EXISTS idx_auth_events_email ON auth_events(email, ts);
"""

# The audit vocabulary (STATUS_SPEC best-practice #6 style: one frozen taxonomy,
# every write validated against it, so readers can branch on `event`).
AUTH_EVENTS = frozenset(
    {
        "code_requested",         # a sign-in code was emailed
        "login_rejected",         # /auth/login refused (not allow-listed / inactive / expired)
        "login_success",          # code verified, session issued
        "login_failed",           # bad, expired, or attempt-exhausted code
        "logout",                 # session revoked by the user
        "roster_reload_applied",  # SIGHUP hot-reload accepted
        "roster_reload_rejected", # SIGHUP hot-reload kept last-good
    }
)


def _now() -> float:
    return time.time()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def norm_email(email: str) -> str:
    return email.strip().lower()


# Sentinel so update_user() can distinguish "leave expiry untouched" from
# "clear expiry" (expires_at=None means no expiry).
_UNSET = object()

# Account-management columns added after the original (email/role/status/
# is_automation/created_at) schema. Each is applied to a live DB by _migrate
# via ALTER TABLE ... ADD COLUMN (a no-op once the column exists). NOT NULL
# columns must carry a DEFAULT so existing rows back-fill.
_ADDED_USER_COLUMNS: dict[str, str] = {
    "name": "TEXT NOT NULL DEFAULT ''",
    "lab_account": "TEXT NOT NULL DEFAULT ''",
    "notes": "TEXT NOT NULL DEFAULT ''",
    "expires_at": "REAL",
    "last_login_at": "REAL",
    "email_verified": "INTEGER NOT NULL DEFAULT 0",
    "disabled_at": "REAL",
    "disabled_reason": "TEXT NOT NULL DEFAULT ''",
}

# Single source of truth for the column list every user SELECT pulls, so
# _row_to_user always receives a fully-populated row.
_USER_COLS = (
    "email, role, status, is_automation, name, lab_account, notes, "
    "expires_at, last_login_at, email_verified, disabled_at, disabled_reason, "
    "created_at"
)


@dataclass(frozen=True)
class User:
    email: str
    role: str
    status: str
    is_automation: bool = False
    # per-scope authorization grants (Phase 1) — a list of roster Grant objects
    # (duck-typed .scope/.id/.role); consulted by authz.effective_*_role. Kept as
    # a bare list here so db.py stays free of a roster import.
    grants: list = field(default_factory=list)
    # account-management metadata (defaulted so positional User(email, role,
    # status, is_automation) construction in tests/authz keeps working)
    name: str = ""
    lab_account: str = ""
    notes: str = ""
    expires_at: Optional[float] = None
    last_login_at: Optional[float] = None
    email_verified: bool = False
    disabled_at: Optional[float] = None
    disabled_reason: str = ""
    created_at: Optional[float] = None

    def is_expired(self, now: Optional[float] = None) -> bool:
        """True once past ``expires_at`` (a lapsed account). No expiry → never."""
        if self.expires_at is None:
            return False
        return (now if now is not None else _now()) > self.expires_at


@dataclass(frozen=True)
class ApiKeyInfo:
    """A machine principal's key (metadata only — the secret is never stored)."""

    id: int
    email: str
    label: str
    created_at: float
    expires_at: Optional[float]
    revoked: bool
    last_used_at: Optional[float] = None


@dataclass(frozen=True)
class AuthEvent:
    """One audit-log row (see :data:`AUTH_EVENTS` for the vocabulary)."""

    id: int
    ts: float
    email: str
    event: str
    detail: str
    ip: str
    user_agent: str


@dataclass(frozen=True)
class SessionInfo:
    """A live session's metadata (the token itself is never exposed)."""

    email: str
    created_at: float
    expires_at: float


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        email=row["email"],
        role=row["role"],
        status=row["status"],
        is_automation=bool(row["is_automation"]),
        name=row["name"],
        lab_account=row["lab_account"],
        notes=row["notes"],
        expires_at=row["expires_at"],
        last_login_at=row["last_login_at"],
        email_verified=bool(row["email_verified"]),
        disabled_at=row["disabled_at"],
        disabled_reason=row["disabled_reason"],
        created_at=row["created_at"],
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
        # Account-management columns. Re-read table_info (the block above may
        # have just altered it) and add any that are missing.
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(users)").fetchall()}
        for col, decl in _ADDED_USER_COLUMNS.items():
            if col not in cols:
                self._conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
        # api_keys.last_used_at — key hygiene: lets an admin tell a dead key
        # from a load-bearing one before revoking.
        key_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(api_keys)").fetchall()}
        if "last_used_at" not in key_cols:
            self._conn.execute("ALTER TABLE api_keys ADD COLUMN last_used_at REAL")
        # One-time backfill: DBs that predate auth_events carry their login
        # history only as never-purged session rows. Project each session into
        # a login_success event so history survives session purging. Runs only
        # while auth_events is empty (idempotent across restarts).
        n_events = self._conn.execute("SELECT COUNT(*) FROM auth_events").fetchone()[0]
        if n_events == 0:
            self._conn.execute(
                "INSERT INTO auth_events (ts, email, event, detail)"
                " SELECT created_at, email, 'login_success', 'backfilled from sessions'"
                " FROM sessions ORDER BY created_at"
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
            # Only the core identity (role/status/is_automation) is upserted;
            # profile columns (name/lab_account/notes/expires_at) are left
            # untouched on conflict so re-adding a user never wipes them — use
            # update_user() to edit those.
            self._conn.execute(
                """INSERT INTO users (email, role, status, is_automation, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(email) DO UPDATE SET
                       role=excluded.role, status=excluded.status,
                       is_automation=excluded.is_automation""",
                (email, role, status, int(is_automation), _now()),
            )
            self._conn.commit()
        return self.get_user(email)  # fully-populated (incl. created_at)

    def update_user(
        self,
        email: str,
        *,
        name: Optional[str] = None,
        lab_account: Optional[str] = None,
        notes: Optional[str] = None,
        expires_at: object = _UNSET,
    ) -> Optional[User]:
        """Patch profile columns. Only arguments that are passed are written
        (``None`` leaves a text field unchanged); pass ``expires_at=None`` to
        clear an expiry, or a float to set it. Returns the updated user."""
        email = norm_email(email)
        sets: list[str] = []
        vals: list[object] = []
        if name is not None:
            sets.append("name=?")
            vals.append(name)
        if lab_account is not None:
            sets.append("lab_account=?")
            vals.append(lab_account)
        if notes is not None:
            sets.append("notes=?")
            vals.append(notes)
        if expires_at is not _UNSET:
            sets.append("expires_at=?")
            vals.append(expires_at)
        if sets:
            vals.append(email)
            with self._lock:
                self._conn.execute(
                    f"UPDATE users SET {', '.join(sets)} WHERE email=?", vals
                )
                self._conn.commit()
        return self.get_user(email)

    def touch_login(self, email: str) -> None:
        """Record a successful sign-in: stamp last_login_at and mark the email
        verified (the code reached the inbox)."""
        with self._lock:
            self._conn.execute(
                "UPDATE users SET last_login_at=?, email_verified=1 WHERE email=?",
                (_now(), norm_email(email)),
            )
            self._conn.commit()

    def get_user(self, email: str) -> Optional[User]:
        email = norm_email(email)
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_USER_COLS} FROM users WHERE email=?", (email,)
            ).fetchone()
        return _row_to_user(row) if row else None

    def list_users(self, *, active_only: bool = False) -> list[User]:
        sql = f"SELECT {_USER_COLS} FROM users"
        if active_only:
            sql += " WHERE status='active'"
        sql += " ORDER BY email"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [_row_to_user(r) for r in rows]

    def set_status(self, email: str, status: str, reason: str = "") -> None:
        """Set a user's status. Disabling stamps disabled_at + disabled_reason;
        re-enabling (any non-disabled status) clears both."""
        email = norm_email(email)
        with self._lock:
            if status == "disabled":
                self._conn.execute(
                    "UPDATE users SET status=?, disabled_at=?, disabled_reason=? WHERE email=?",
                    (status, _now(), reason, email),
                )
            else:
                self._conn.execute(
                    "UPDATE users SET status=?, disabled_at=NULL, disabled_reason='' WHERE email=?",
                    (status, email),
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

    def login_code_rate(
        self, email: str, window_s: float
    ) -> tuple[int, Optional[float], Optional[float]]:
        """Send-rate stats for ``email`` over the last ``window_s`` seconds, used
        to throttle code emails (anti-spam): ``(count, oldest_at, latest_at)``
        of login codes created in the window. ``oldest/latest`` are ``None`` when
        count is 0. Counts *sends*, not verify attempts."""
        cutoff = _now() - window_s
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, MIN(created_at) AS oldest, MAX(created_at) AS latest"
                " FROM login_codes WHERE email=? AND created_at>=?",
                (norm_email(email), cutoff),
            ).fetchone()
        return (row["n"], row["oldest"], row["latest"])

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

    def verify_api_key(self, token: str) -> Optional[str]:
        """Return the principal's **email** for a live (un-revoked, unexpired)
        key, else ``None``. The api_keys table only proves possession of a valid
        key; the caller resolves that email to a principal via the roster (and
        checks it is an approved, active automation account)."""
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
            self._conn.execute(
                "UPDATE api_keys SET last_used_at=? WHERE key_hash=?", (_now(), _hash(token))
            )
            self._conn.commit()
        return row["email"]

    def last_login_at(self, email: str) -> Optional[float]:
        """Most recent successful sign-in for an email, from the audit log
        (pre-audit-log history was backfilled from sessions in _migrate)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(ts) AS t FROM auth_events WHERE email=? AND event='login_success'",
                (norm_email(email),),
            ).fetchone()
        return row["t"] if row and row["t"] is not None else None

    def _rows_to_keys(self, rows) -> list[ApiKeyInfo]:
        return [
            ApiKeyInfo(
                r["id"], r["email"], r["label"], r["created_at"],
                r["expires_at"], bool(r["revoked"]), r["last_used_at"],
            )
            for r in rows
        ]

    def list_api_keys(self, email: str) -> list[ApiKeyInfo]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, email, label, created_at, expires_at, revoked, last_used_at"
                " FROM api_keys WHERE email=? ORDER BY id",
                (norm_email(email),),
            ).fetchall()
        return self._rows_to_keys(rows)

    def list_all_api_keys(self) -> list[ApiKeyInfo]:
        """Every key across all principals — the admin page's key-hygiene view."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, email, label, created_at, expires_at, revoked, last_used_at"
                " FROM api_keys ORDER BY email, id"
            ).fetchall()
        return self._rows_to_keys(rows)

    def revoke_api_key(self, key_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (key_id,))
            self._conn.commit()

    # ---- audit log (auth_events) --------------------------------------------

    def record_auth_event(
        self,
        event: str,
        email: str = "",
        *,
        detail: str = "",
        ip: str = "",
        user_agent: str = "",
    ) -> None:
        """Append one audit row. ``event`` must be in :data:`AUTH_EVENTS` so the
        vocabulary can't drift silently."""
        if event not in AUTH_EVENTS:
            raise ValueError(f"unknown auth event {event!r} (add it to AUTH_EVENTS)")
        with self._lock:
            self._conn.execute(
                "INSERT INTO auth_events (ts, email, event, detail, ip, user_agent)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (_now(), norm_email(email) if email else "", event, detail, ip, user_agent[:200]),
            )
            self._conn.commit()

    def list_auth_events(
        self, *, email: Optional[str] = None, limit: int = 100
    ) -> list[AuthEvent]:
        """Newest-first audit rows, optionally filtered to one account."""
        sql = "SELECT id, ts, email, event, detail, ip, user_agent FROM auth_events"
        params: tuple = ()
        if email:
            sql += " WHERE email=?"
            params = (norm_email(email),)
        sql += " ORDER BY id DESC LIMIT ?"
        params += (max(1, limit),)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            AuthEvent(r["id"], r["ts"], r["email"], r["event"], r["detail"], r["ip"], r["user_agent"])
            for r in rows
        ]

    def list_active_sessions(self) -> list[SessionInfo]:
        """Live (unexpired) sessions, newest first — "who is signed in right now"."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT email, created_at, expires_at FROM sessions"
                " WHERE expires_at > ? ORDER BY created_at DESC",
                (_now(),),
            ).fetchall()
        return [SessionInfo(r["email"], r["created_at"], r["expires_at"]) for r in rows]

    def count_active_sessions(self, email: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE email=? AND expires_at > ?",
                (norm_email(email), _now()),
            ).fetchone()
        return row["n"]

    # ---- housekeeping --------------------------------------------------------

    def purge_expired(self, *, older_than_days: float = 7.0) -> tuple[int, int]:
        """Delete expired sessions and stale login codes older than the grace
        window. Safe now that auth_events is the durable login record (sessions
        and codes are working state). Returns (sessions_purged, codes_purged)."""
        cutoff = _now() - older_than_days * 86400.0
        with self._lock:
            c1 = self._conn.execute(
                "DELETE FROM sessions WHERE expires_at < ?", (cutoff,)
            ).rowcount
            c2 = self._conn.execute(
                "DELETE FROM login_codes WHERE created_at < ?", (cutoff,)
            ).rowcount
            self._conn.commit()
        return (c1, c2)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
