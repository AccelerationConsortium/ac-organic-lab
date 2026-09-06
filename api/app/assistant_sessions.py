"""Saved planning sessions for the lab assistant — Plan mode.

``docs/ASSISTANT_PERSISTENCE.md`` step 2. Ask and Control conversations are
temporary and live only in the browser tab; **Plan** is the third mode, whose
conversations are named, owner-private sessions stored on the dashboard host so
an operator can develop a reusable protocol over days and come back to it.

What this module owns
---------------------
* ``AssistantSessionStore`` — the ``assistant.db`` SQLite file (WAL, one
  serialised writer, ``PRAGMA user_version`` migrations, bounded retention).
  Deliberately separate from the public telemetry in ``lab.db`` and from the
  scientific record in BitacoraDB (D-1, D-3): a chat transcript is neither.
* ``build_assistant_sessions_router`` — ``/api/assistant/sessions/*``: list,
  create, read, rename, delete, export, and ``POST …/turns`` which runs one
  chat turn *inside* a session and persists it.

Trust story (unchanged from Ask/Control)
----------------------------------------
* Identity is the middleware-injected ``X-Auth-User`` / ``X-Auth-Role``, never
  a body field (``web/src/middleware.ts`` deletes any client-supplied copy).
  Every route resolves the principal first. The owner has full access; a
  global admin may **read** (list with ``scope=all``, open, export) but never
  acts as the owner — rename, delete and turns are 403 for an admin who is not
  the owner. Anyone else gets 404, so the id space leaks nothing.
* Plan's toolset is Ask's: the read-only ``lab-history`` + ``lab-inventory``
  servers. ``lab-control`` is never registered, so no proposal card can be
  produced here and nothing in a saved session can actuate hardware (D-9).
  Historical proposal cards imported from a temporary conversation are stored
  as text/events and restored **inertly** — display only, never approvable.
* Model context is rebuilt **server-side** from the stored session plus the
  new message (D-2). The client sends only its new text and a request id; it
  cannot replace history or assert authorship. Retained history and context
  size are independent (``CONTEXT_MESSAGES`` vs the retention limits).
* One active turn per session; a second concurrent ``POST …/turns`` is 409.
  Retrying the same ``request_id`` replays the stored turn without rerunning
  tools. A turn that is cut off (client gone, service restart) is recorded as
  ``interrupted`` — never silently promoted to ``completed``.

Saving a conversation files nothing: the export carries the same
non-executable notice as the temporary-chat download, and no route here can
register a protocol or start a run. That is done in bitácora through project
review (D-5…D-8, later build-order steps).

Configuration
-------------
* ``ASSISTANT_DB_PATH`` — the SQLite file (default: ``assistant.db`` beside the
  resolved ``lab.db``, so it lands in the same ``data/`` the unit can write).
* ``ASSISTANT_SESSION_RETENTION_DAYS`` — sessions untouched for longer are
  purged on open and on create (default 180; 0 disables).
* ``ASSISTANT_MAX_SESSIONS_PER_OWNER`` — creation is refused past this
  (default 200).
* ``ASSISTANT_MAX_MESSAGES_PER_SESSION`` — turns are refused past this
  (default 2000).
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import assistant as _assistant
from .assistant import SSE_PREAMBLE, ChatMessage, _sse
from .db import resolve_db_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

# How much stored history the model sees per turn. Same figure as the temporary
# bubble sends (AssistantBubble.tsx slices its history to 40), so Plan answers
# are grounded in the same window; retention is a separate, much larger limit.
CONTEXT_MESSAGES = 40
MAX_TEXT_CHARS = 20_000
MAX_TITLE_CHARS = 120
MAX_SEED_MESSAGES = 40
RETENTION_DAYS = int(os.environ.get("ASSISTANT_SESSION_RETENTION_DAYS", "180"))
MAX_SESSIONS_PER_OWNER = int(os.environ.get("ASSISTANT_MAX_SESSIONS_PER_OWNER", "200"))
MAX_MESSAGES_PER_SESSION = int(os.environ.get("ASSISTANT_MAX_MESSAGES_PER_SESSION", "2000"))
# A turn still marked running this long after it began was orphaned by a lost
# client or a restart the recovery sweep missed; it is marked interrupted and
# the session unlocked. One wallclock cap plus slack.
STALE_TURN_S = _assistant.DEFAULT_TIMEOUT_S + 60.0

MessageState = Literal["completed", "running", "interrupted", "failed"]

EXPORT_NOTICE = (
    "Saved planning conversation export; not a registered Plan, a protocol, or "
    "proof of execution, and nothing in it can be replayed. Saving a conversation "
    "files nothing — protocols are edited and registered in bitácora through "
    "project review. Camera images are linked, not embedded; snapshot links expire "
    "after about 24 hours. Any imported proposals or control actions remain in the "
    "audit trail, which this export does not replace."
)

# Appended to SYSTEM_PROMPT for every Plan turn. The toolset is Ask's; this
# tells the model what the mode is FOR and what it must never claim.
PLAN_PROMPT_ADDENDUM = """

PLAN MODE IS ACTIVE.
This is a saved planning session: the operator is developing a reusable
protocol or workflow, and the conversation is stored so they can return to it.
Your tools are the same read-only lab-history and lab-inventory tools as Ask
mode. You have no propose or control tools here, and nothing you write can
actuate hardware, schedule a run, or register anything. When the user wants a
step actually performed, say so plainly and point them at Control mode (single
supervised actions) or at a validated, human-authorized workflow plan in
bitácora (anything larger, multi-device, or unattended).

Draft protocols as ordered, numbered steps with explicit equipment ids,
labware, volumes, temperatures, and timings, and name what is still unknown or
unverified rather than inventing it. Never state that a protocol was filed,
registered, saved to bitácora, or executed: saving this conversation files
nothing. It is fine to be longer here than in Ask mode while the user is
drafting; still lead with the substance."""


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class SessionLimit(Exception):
    """The owner already holds the maximum number of saved sessions."""


class SessionFull(Exception):
    """The session already holds the maximum number of messages."""


class TurnInProgress(Exception):
    """Another turn is running in this session (one active turn per session)."""


class RevisionConflict(Exception):
    """The caller's expected revision is stale — concurrent edit."""


_MIGRATIONS: list[str] = [
    # v1 — 2026-09-06, step 2 of ASSISTANT_PERSISTENCE.md.
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id             TEXT PRIMARY KEY,
        owner          TEXT NOT NULL,
        title          TEXT NOT NULL,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        revision       INTEGER NOT NULL DEFAULT 0,
        message_count  INTEGER NOT NULL DEFAULT 0,
        active_turn_id TEXT,
        active_turn_started_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner, updated_at);

    CREATE TABLE IF NOT EXISTS messages (
        id          TEXT PRIMARY KEY,
        session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        seq         INTEGER NOT NULL,
        turn_id     TEXT NOT NULL,
        role        TEXT NOT NULL CHECK(role IN ('user','assistant')),
        state       TEXT NOT NULL
                    CHECK(state IN ('completed','running','interrupted','failed')),
        text        TEXT NOT NULL,
        events      TEXT NOT NULL DEFAULT '[]',
        error       TEXT,
        mode        TEXT NOT NULL DEFAULT 'plan',
        imported    INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        UNIQUE(session_id, seq)
    );
    CREATE INDEX IF NOT EXISTS idx_messages_turn ON messages(session_id, turn_id);

    -- Idempotency: one client request id maps to exactly one turn forever.
    CREATE TABLE IF NOT EXISTS turn_requests (
        session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        request_id  TEXT NOT NULL,
        turn_id     TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (session_id, request_id)
    );
    """,
]


def resolve_sessions_db_path() -> Path:
    env = os.environ.get("ASSISTANT_DB_PATH")
    if env:
        return Path(env)
    return resolve_db_path().with_name("assistant.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _row(cur_row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(cur_row) if cur_row is not None else None


class AssistantSessionStore:
    """Thread-safe store for saved planning sessions.

    Synchronous like :class:`app.db.LabDatabase`; async callers wrap methods
    with ``run_in_executor``. Every write holds ``_lock`` and commits in one
    transaction, so a session is never observed half-updated.
    """

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        for i, sql in enumerate(_MIGRATIONS[version:], start=version + 1):
            conn.executescript("BEGIN;" + sql + f"PRAGMA user_version={i};COMMIT;")
        self._conn = conn
        self._recover_interrupted()
        self.sweep()
        logger.info("Assistant sessions store open: %s (schema v%d)", self._path, len(_MIGRATIONS))

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("AssistantSessionStore is not open")
        return self._conn

    def _recover_interrupted(self) -> None:
        """A restart never turns an unfinished answer into a finished one:
        anything still ``running`` when the store opens was cut off."""

        with self._lock:
            now = _now()
            cur = self.conn.execute(
                "UPDATE messages SET state='interrupted', updated_at=? WHERE state='running'",
                (now,),
            )
            self.conn.execute(
                "UPDATE sessions SET active_turn_id=NULL, active_turn_started_at=NULL "
                "WHERE active_turn_id IS NOT NULL"
            )
            if cur.rowcount:
                logger.warning(
                    "assistant sessions: %d turn(s) were running at shutdown; marked interrupted",
                    cur.rowcount,
                )

    def sweep(self) -> int:
        """Purge sessions untouched for longer than the retention window."""

        if RETENTION_DAYS <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat(
            timespec="microseconds"
        )
        with self._lock:
            cur = self.conn.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,))
            if cur.rowcount:
                logger.info("assistant sessions: purged %d session(s) past retention", cur.rowcount)
            return int(cur.rowcount or 0)

    # -- sessions ----------------------------------------------------------

    def _session_row(self, session_id: str) -> dict[str, Any] | None:
        return _row(self.conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone())

    @staticmethod
    def _public_session(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "owner": row["owner"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revision": int(row["revision"]),
            "message_count": int(row["message_count"]),
            "active_turn": row["active_turn_id"] is not None,
        }

    def create_session(
        self,
        owner: str,
        title: str,
        seed: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a session, optionally seeded with messages the owner chose
        to carry over from a temporary conversation. Seeded messages are
        stored ``imported`` and ``completed``: text plus display events, never
        anything approvable."""

        self.sweep()
        title = (title or "").strip()[:MAX_TITLE_CHARS] or "Untitled plan"
        now = _now()
        sid = _new_id("ps")
        with self._lock:
            held = self.conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE owner=?", (owner,)
            ).fetchone()[0]
            if held >= MAX_SESSIONS_PER_OWNER:
                raise SessionLimit(held)
            self.conn.execute("BEGIN")
            try:
                self.conn.execute(
                    "INSERT INTO sessions(id, owner, title, created_at, updated_at, revision, "
                    "message_count) VALUES (?,?,?,?,?,1,0)",
                    (sid, owner, title, now, now),
                )
                seq = 0
                for m in seed or []:
                    seq += 1
                    turn_id = _new_id("pt")
                    self.conn.execute(
                        "INSERT INTO messages(id, session_id, seq, turn_id, role, state, text, "
                        "events, error, mode, imported, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)",
                        (
                            _new_id("pm"),
                            sid,
                            seq,
                            turn_id,
                            m["role"],
                            m.get("state") or "completed",
                            str(m.get("text") or "")[:MAX_TEXT_CHARS],
                            json.dumps(m.get("events") or [], default=str),
                            m.get("error"),
                            m.get("mode") or "ask",
                            now,
                            now,
                        ),
                    )
                if seq:
                    self.conn.execute(
                        "UPDATE sessions SET message_count=? WHERE id=?", (seq, sid)
                    )
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
            row = self._session_row(sid)
        assert row is not None
        return self._public_session(row)

    def list_sessions(self, owner: str | None = None) -> list[dict[str, Any]]:
        """``owner=None`` lists everyone's — for a global admin's read view."""

        if owner is None:
            rows = self.conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM sessions WHERE owner=? ORDER BY updated_at DESC", (owner,)
            ).fetchall()
        return [self._public_session(dict(r)) for r in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self._session_row(session_id)
        return self._public_session(row) if row else None

    def rename_session(self, session_id: str, title: str, expected_revision: int) -> dict[str, Any]:
        title = (title or "").strip()[:MAX_TITLE_CHARS]
        if not title:
            raise ValueError("title must not be empty")
        with self._lock:
            row = self._session_row(session_id)
            if row is None:
                raise KeyError(session_id)
            if int(row["revision"]) != expected_revision:
                raise RevisionConflict(int(row["revision"]))
            self.conn.execute(
                "UPDATE sessions SET title=?, revision=revision+1, updated_at=? WHERE id=?",
                (title, _now(), session_id),
            )
            row = self._session_row(session_id)
        assert row is not None
        return self._public_session(row)

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            return bool(cur.rowcount)

    # -- messages ----------------------------------------------------------

    @staticmethod
    def _public_message(row: dict[str, Any]) -> dict[str, Any]:
        try:
            events = json.loads(row["events"] or "[]")
        except (TypeError, ValueError):
            events = []
        return {
            "id": row["id"],
            "seq": int(row["seq"]),
            "turn_id": row["turn_id"],
            "role": row["role"],
            "state": row["state"],
            "text": row["text"],
            "events": events if isinstance(events, list) else [],
            "error": row["error"],
            "mode": row["mode"],
            "imported": bool(row["imported"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY seq", (session_id,)
        ).fetchall()
        return [self._public_message(dict(r)) for r in rows]

    def get_turn(self, session_id: str, turn_id: str) -> dict[str, Any] | None:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE session_id=? AND turn_id=? ORDER BY seq",
            (session_id, turn_id),
        ).fetchall()
        if not rows:
            return None
        msgs = [self._public_message(dict(r)) for r in rows]
        user = next((m for m in msgs if m["role"] == "user"), None)
        answer = next((m for m in msgs if m["role"] == "assistant"), None)
        return {"turn_id": turn_id, "user": user, "assistant": answer}

    def context_messages(self, session_id: str, limit: int = CONTEXT_MESSAGES) -> list[dict[str, str]]:
        """The model's view of the session: the last ``limit`` stored messages
        that carry text and are not still running. Built here, from storage —
        never from the client."""

        rows = self.conn.execute(
            "SELECT role, text FROM messages WHERE session_id=? AND state!='running' "
            "AND text!='' ORDER BY seq DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [{"role": r["role"], "content": r["text"]} for r in reversed(rows)]

    def begin_turn(self, session_id: str, request_id: str, text: str) -> tuple[dict[str, Any], bool]:
        """Record the user's message and an empty ``running`` answer.

        Returns ``(turn, created)``. A repeated ``request_id`` returns the
        existing turn with ``created=False`` and changes nothing — the caller
        replays it instead of rerunning tools. Raises :class:`TurnInProgress`
        while another turn is live, :class:`SessionFull` at the message cap.
        """

        text = (text or "").strip()[:MAX_TEXT_CHARS]
        with self._lock:
            session = self._session_row(session_id)
            if session is None:
                raise KeyError(session_id)
            prior = self.conn.execute(
                "SELECT turn_id FROM turn_requests WHERE session_id=? AND request_id=?",
                (session_id, request_id),
            ).fetchone()
            if prior is not None:
                turn = self.get_turn(session_id, prior["turn_id"])
                assert turn is not None
                return turn, False
            active = session["active_turn_id"]
            if active is not None:
                started = session["active_turn_started_at"]
                stale = True
                if started:
                    try:
                        age = (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds()
                        stale = age > STALE_TURN_S
                    except ValueError:
                        stale = True
                if not stale:
                    raise TurnInProgress(active)
                # Orphaned by a lost client; say so on the record and unlock.
                self.conn.execute(
                    "UPDATE messages SET state='interrupted', updated_at=? "
                    "WHERE session_id=? AND turn_id=? AND state='running'",
                    (_now(), session_id, active),
                )
            if int(session["message_count"]) + 2 > MAX_MESSAGES_PER_SESSION:
                raise SessionFull(int(session["message_count"]))
            now = _now()
            turn_id = _new_id("pt")
            seq = int(session["message_count"])
            self.conn.execute("BEGIN")
            try:
                self.conn.execute(
                    "INSERT INTO messages(id, session_id, seq, turn_id, role, state, text, events, "
                    "mode, created_at, updated_at) VALUES (?,?,?,?,'user','completed',?,'[]','plan',?,?)",
                    (_new_id("pm"), session_id, seq + 1, turn_id, text, now, now),
                )
                self.conn.execute(
                    "INSERT INTO messages(id, session_id, seq, turn_id, role, state, text, events, "
                    "mode, created_at, updated_at) VALUES (?,?,?,?,'assistant','running','','[]','plan',?,?)",
                    (_new_id("pm"), session_id, seq + 2, turn_id, now, now),
                )
                self.conn.execute(
                    "INSERT INTO turn_requests(session_id, request_id, turn_id, created_at) "
                    "VALUES (?,?,?,?)",
                    (session_id, request_id, turn_id, now),
                )
                self.conn.execute(
                    "UPDATE sessions SET message_count=message_count+2, revision=revision+1, "
                    "updated_at=?, active_turn_id=?, active_turn_started_at=? WHERE id=?",
                    (now, turn_id, now, session_id),
                )
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
            turn = self.get_turn(session_id, turn_id)
        assert turn is not None
        return turn, True

    def finish_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        text: str,
        events: list[dict[str, Any]],
        state: MessageState,
        error: str | None = None,
    ) -> None:
        """Close the answer half of a turn with its final state and unlock the
        session. Idempotent: a second call for a turn already closed by the
        recovery sweep still records the text that did arrive."""

        with self._lock:
            now = _now()
            self.conn.execute("BEGIN")
            try:
                self.conn.execute(
                    "UPDATE messages SET text=?, events=?, state=?, error=?, updated_at=? "
                    "WHERE session_id=? AND turn_id=? AND role='assistant'",
                    (
                        text[:MAX_TEXT_CHARS],
                        json.dumps(events, default=str),
                        state,
                        (error or None) and str(error)[:2000],
                        now,
                        session_id,
                        turn_id,
                    ),
                )
                self.conn.execute(
                    "UPDATE sessions SET revision=revision+1, updated_at=?, "
                    "active_turn_id=CASE WHEN active_turn_id=? THEN NULL ELSE active_turn_id END, "
                    "active_turn_started_at=CASE WHEN active_turn_id IS NULL THEN NULL "
                    "ELSE active_turn_started_at END WHERE id=?",
                    (now, turn_id, session_id),
                )
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    # -- export ------------------------------------------------------------

    def export(self, session_id: str) -> dict[str, Any] | None:
        session = self.get_session(session_id)
        if session is None:
            return None
        messages = self.list_messages(session_id)
        return {
            "schema_version": 1,
            "record_type": "planning_session_export",
            "executable": False,
            "exported_at": _now(),
            "notice": EXPORT_NOTICE,
            "session": session,
            "messages": [
                {
                    "seq": m["seq"],
                    "role": m["role"],
                    "mode": m["mode"],
                    "imported": m["imported"],
                    "text": m["text"],
                    "completion": m["state"],
                    "error": m["error"],
                    "events": m["events"],
                    "created_at": m["created_at"],
                }
                for m in messages
            ],
        }


def _fenced(text: str, language: str = "") -> str:
    longest = 2
    run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    fence = "`" * (longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"


def export_markdown(export: dict[str, Any]) -> str:
    session = export["session"]
    parts = [
        f"# Planning session: {session['title']}",
        f"Owner: {session['owner']}  \nExported: {export['exported_at']}  \n"
        f"Created: {session['created_at']}  \nLast updated: {session['updated_at']}",
        export["notice"],
    ]
    for m in export["messages"]:
        label = f"{m['role']} ({m['mode']}{'; imported' if m['imported'] else ''}; {m['completion']})"
        details = {k: m[k] for k in ("error", "events", "created_at") if m.get(k)}
        block = [f"## {m['seq']}. {label}", _fenced(m["text"] or "")]
        if details:
            block.append(_fenced(json.dumps(details, indent=2, default=str), "json"))
        parts.append("\n\n".join(block))
    parts.append("")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class SeedMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=MAX_TEXT_CHARS)
    mode: Literal["ask", "control", "plan"] = "ask"
    completion: Literal["completed", "interrupted", "failed"] = "completed"
    error: str | None = Field(default=None, max_length=2000)
    # Display-only projections (tool names, image links, imported control
    # events). Stored verbatim as a list and rendered inertly; a card is never
    # rebuilt from them.
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


class CreateSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    seed: list[SeedMessage] = Field(default_factory=list, max_length=MAX_SEED_MESSAGES)


class RenameSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    revision: int = Field(ge=0)


class TurnRequest(BaseModel):
    # Client-minted idempotency key: a retry of the same request replays the
    # stored turn instead of running a second one.
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


def _store(request: Request) -> AssistantSessionStore:
    store = getattr(request.app.state, "assistant_sessions", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Saved planning sessions are unavailable on this dashboard host; Ask and Control still work.",
        )
    return store


def _principal(request: Request) -> tuple[str, str]:
    """The middleware-verified identity. No identity, no saved sessions — even
    under the DASHBOARD_CONTROL_OPEN dev bypass, because ownership has to come
    from auth (D-1)."""

    user = (request.headers.get("x-auth-user") or "").strip()
    role = (request.headers.get("x-auth-role") or "").strip().lower()
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to use saved planning sessions.")
    return user, role


async def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


def _owned(
    store: AssistantSessionStore,
    session_id: str,
    user: str,
    role: str,
    *,
    write: bool,
) -> dict[str, Any]:
    """The session if this principal may act on it, else the right refusal.

    Owner: anything. Global admin: read only — the D-1 rule that an admin
    never acts as the owner. Everyone else: 404, so ids leak nothing.
    """

    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="no such saved session")
    if session["owner"] == user:
        return session
    if role == "admin":
        if write:
            raise HTTPException(
                status_code=403,
                detail="admins may read another person's saved session but never act as its owner",
            )
        return session
    raise HTTPException(status_code=404, detail="no such saved session")


def _decode_frame(frame: bytes) -> dict[str, Any] | None:
    """The dict inside one ``data: {...}\\n\\n`` SSE frame, or None for
    comments / anything else. Both backends emit frames through ``_sse``."""

    if not frame.startswith(b"data:"):
        return None
    try:
        payload = json.loads(frame[5:].strip().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


class _TurnProjection:
    """Accumulates what a turn produced, in the display-only shape the store
    keeps: full text, tool pills, image links, refusal/decline chips. Raw tool
    payloads never pass through here (D-2)."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.events: list[dict[str, Any]] = []
        self.terminal: str | None = None  # "done" | "error"
        self.error: str | None = None

    def absorb(self, frame: dict[str, Any]) -> None:
        kind = frame.get("type")
        if kind == "text" and isinstance(frame.get("delta"), str):
            self.text.append(frame["delta"])
        elif kind == "tool_use" and isinstance(frame.get("name"), str):
            self.events.append({"type": "tool", "name": frame["name"], "ok": False})
        elif kind == "tool_result":
            name = frame.get("name")
            for ev in reversed(self.events):
                if ev.get("type") == "tool" and not ev.get("ok") and (
                    name == "tool" or ev.get("name") == name
                ):
                    ev["ok"] = True
                    break
        elif kind == "image" and isinstance(frame.get("image"), dict):
            im = frame["image"]
            url = im.get("url") or im.get("image_url")
            if isinstance(url, str):
                self.events.append(
                    {
                        "type": "image",
                        "url": url,
                        "camera_id": im.get("camera_id"),
                        "camera_name": im.get("camera_name"),
                        "lens": im.get("lens"),
                        "taken_at": im.get("taken_at"),
                    }
                )
        elif kind == "proposal_refused" and isinstance(frame.get("refusal"), dict):
            self.events.append({"type": "refusal", **frame["refusal"]})
        elif kind == "declined" and isinstance(frame.get("declined"), dict):
            self.events.append({"type": "declined", **frame["declined"]})
        elif kind == "done":
            self.terminal = "done"
        elif kind == "error":
            self.terminal = "error"
            self.error = str(frame.get("message") or "error")

    @property
    def full_text(self) -> str:
        return "".join(self.text)


def _replay_frames(turn: dict[str, Any], session_id: str) -> list[bytes]:
    """The stored turn as the SSE frames a live run would have produced —
    what a retried ``request_id`` gets instead of a second model run."""

    answer = turn.get("assistant") or {}
    out = [
        SSE_PREAMBLE,
        _sse({"type": "session", "session_id": session_id, "turn_id": turn["turn_id"], "replayed": True}),
    ]
    for ev in answer.get("events") or []:
        if ev.get("type") == "tool":
            out.append(_sse({"type": "tool_use", "name": ev.get("name") or "tool"}))
            if ev.get("ok"):
                out.append(_sse({"type": "tool_result", "name": ev.get("name") or "tool"}))
        elif ev.get("type") == "image":
            out.append(_sse({"type": "image", "image": {k: v for k, v in ev.items() if k != "type"}}))
    if answer.get("text"):
        out.append(_sse({"type": "text", "delta": answer["text"]}))
    state = answer.get("state")
    if state == "completed":
        out.append(_sse({"type": "done"}))
    elif state == "failed":
        out.append(_sse({"type": "error", "message": answer.get("error") or "this turn failed"}))
    elif state == "running":
        out.append(
            _sse(
                {
                    "type": "error",
                    "message": "This turn is still running (another tab, or a reconnect). "
                    "Reopen the session to see its result.",
                }
            )
        )
    else:
        out.append(_sse({"type": "interrupted"}))
    return out


def build_assistant_sessions_router() -> APIRouter:
    router = APIRouter(prefix="/api/assistant/sessions", tags=["assistant"])

    @router.get("")
    async def list_sessions(
        request: Request, scope: Literal["mine", "all"] = Query(default="mine")
    ) -> dict[str, Any]:
        store = _store(request)
        user, role = _principal(request)
        if scope == "all":
            if role != "admin":
                raise HTTPException(status_code=403, detail="scope=all is for global admins")
            sessions = await _run(store.list_sessions, None)
        else:
            sessions = await _run(store.list_sessions, user)
        return {"sessions": sessions, "owner": user, "scope": scope}

    @router.post("", status_code=201)
    async def create_session(request: Request, body: CreateSessionRequest) -> dict[str, Any]:
        store = _store(request)
        user, _role = _principal(request)
        seed = [
            {
                "role": m.role,
                "text": m.text,
                "mode": m.mode,
                "state": m.completion,
                "error": m.error,
                "events": m.events,
            }
            for m in body.seed
        ]
        try:
            session = await _run(store.create_session, user, body.title, seed)
        except SessionLimit as exc:
            raise HTTPException(
                status_code=409,
                detail=f"You already have {exc.args[0]} saved sessions; delete one to create another.",
            ) from exc
        logger.info(
            "assistant plan session created: user=%s session=%s seeded=%d", user, session["id"], len(seed)
        )
        return session

    @router.get("/{session_id}")
    async def read_session(session_id: str, request: Request) -> dict[str, Any]:
        store = _store(request)
        user, role = _principal(request)
        session = await _run(_owned, store, session_id, user, role, write=False)
        messages = await _run(store.list_messages, session_id)
        return {"session": session, "messages": messages, "read_only": session["owner"] != user}

    @router.patch("/{session_id}")
    async def rename_session(
        session_id: str, request: Request, body: RenameSessionRequest
    ) -> dict[str, Any]:
        store = _store(request)
        user, role = _principal(request)
        await _run(_owned, store, session_id, user, role, write=True)
        try:
            return await _run(store.rename_session, session_id, body.title, body.revision)
        except RevisionConflict as exc:
            raise HTTPException(
                status_code=409,
                detail=f"this session changed in another tab (revision {exc.args[0]}); reload it",
            ) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="no such saved session") from exc

    @router.delete("/{session_id}", status_code=204)
    async def delete_session(session_id: str, request: Request) -> Response:
        store = _store(request)
        user, role = _principal(request)
        await _run(_owned, store, session_id, user, role, write=True)
        await _run(store.delete_session, session_id)
        logger.info("assistant plan session deleted: user=%s session=%s", user, session_id)
        return Response(status_code=204)

    @router.get("/{session_id}/export")
    async def export_session(
        session_id: str, request: Request, format: Literal["json", "md"] = Query(default="json")
    ) -> Response:
        store = _store(request)
        user, role = _principal(request)
        session = await _run(_owned, store, session_id, user, role, write=False)
        export = await _run(store.export, session_id)
        if export is None:
            raise HTTPException(status_code=404, detail="no such saved session")
        stamp = export["exported_at"].replace(":", "-").replace(".", "-")
        safe_title = "".join(c if c.isalnum() or c in "-_" else "-" for c in session["title"])[:40]
        filename = f"plan-{safe_title or 'session'}-{stamp}.{format}"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        if format == "md":
            return Response(export_markdown(export), media_type="text/markdown; charset=utf-8", headers=headers)
        return Response(
            json.dumps(export, indent=2, default=str), media_type="application/json", headers=headers
        )

    @router.post("/{session_id}/turns")
    async def run_turn(session_id: str, request: Request, body: TurnRequest) -> StreamingResponse:
        """One chat turn inside a saved session, streamed like ``/chat``.

        The server stores the user's message first, rebuilds the model's
        context from the session, runs the turn on the **Ask** toolset with
        the Plan prompt, and closes the answer with its truthful final state
        — including ``interrupted`` when the browser goes away mid-answer.
        """

        store = _store(request)
        user, role = _principal(request)
        await _run(_owned, store, session_id, user, role, write=True)

        try:
            turn, created = await _run(store.begin_turn, session_id, body.request_id, body.text)
        except TurnInProgress as exc:
            raise HTTPException(
                status_code=409,
                detail="a turn is already running in this session (another tab?); wait for it to finish",
            ) from exc
        except SessionFull as exc:
            raise HTTPException(
                status_code=409,
                detail=f"this session holds {exc.args[0]} messages, the maximum; start a new session",
            ) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="no such saved session") from exc

        sse_headers = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}

        if not created:
            logger.info(
                "assistant plan turn replayed: user=%s session=%s turn=%s", user, session_id, turn["turn_id"]
            )

            async def replay() -> AsyncIterator[bytes]:
                for frame in _replay_frames(turn, session_id):
                    yield frame

            return StreamingResponse(replay(), media_type="text/event-stream", headers=sse_headers)

        # Plan runs on the Ask backend and toolset: lab-control is never
        # registered, so the model cannot produce a proposal card here.
        backend = _assistant.DEFAULT_BACKEND
        if backend == "openai":
            from . import assistant_openai

            if assistant_openai.api_key() is None:
                store.finish_turn(
                    session_id, turn["turn_id"], text="", events=[], state="failed",
                    error="ASSISTANT_OPENAI_API_KEY is not set on the dashboard host",
                )
                raise HTTPException(status_code=503, detail="ASSISTANT_OPENAI_API_KEY is not set on the dashboard host")
            runner = assistant_openai.run_openai_turn
        else:
            if _assistant._claude_binary() is None:
                store.finish_turn(
                    session_id, turn["turn_id"], text="", events=[], state="failed",
                    error="claude CLI is not installed on the dashboard host",
                )
                raise HTTPException(status_code=503, detail="claude CLI is not installed on the dashboard host")
            runner = _assistant._run_claude

        history = await _run(store.context_messages, session_id, CONTEXT_MESSAGES)
        messages = [ChatMessage(role=m["role"], content=m["content"]) for m in history]
        if not messages or messages[-1].role != "user":
            # context_messages() excludes empty texts; the user text was
            # validated non-empty, so this cannot happen — guard anyway.
            messages.append(ChatMessage(role="user", content=body.text.strip()[:MAX_TEXT_CHARS]))

        logger.info(
            "assistant chat: user=%s mode=plan backend=%s session=%s turn=%s context=%d",
            user, backend, session_id, turn["turn_id"], len(messages),
        )
        turn_id = turn["turn_id"]

        async def gen() -> AsyncIterator[bytes]:
            projection = _TurnProjection()
            state: MessageState = "interrupted"
            yield SSE_PREAMBLE
            yield _sse({"type": "session", "session_id": session_id, "turn_id": turn_id})
            try:
                async for frame in runner(
                    messages,
                    control=False,
                    actor=user,
                    extra_system_prompt=PLAN_PROMPT_ADDENDUM,
                ):
                    decoded = _decode_frame(frame)
                    if decoded is not None:
                        projection.absorb(decoded)
                    yield frame
                if projection.terminal == "done":
                    state = "completed"
                elif projection.terminal == "error":
                    state = "failed"
                else:
                    # The engine stopped without a terminal frame. Say so to
                    # the browser too, so it does not report "connection lost".
                    state = "interrupted"
                    yield _sse({"type": "interrupted"})
            except asyncio.CancelledError:
                state = "interrupted"
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("assistant plan turn errored")
                state = "failed"
                projection.error = str(exc)
                yield _sse({"type": "error", "message": str(exc)})
            finally:
                # Synchronous on purpose: this must land even when the task is
                # being cancelled because the client disconnected, and a
                # few-millisecond sqlite write is cheaper than losing the
                # record of what was said.
                try:
                    store.finish_turn(
                        session_id,
                        turn_id,
                        text=projection.full_text,
                        events=projection.events,
                        state=state,
                        error=projection.error,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("assistant plan turn could not be persisted")
                logger.info(
                    "assistant plan turn done: user=%s session=%s turn=%s state=%s chars=%d tools=%d",
                    user, session_id, turn_id, state, len(projection.full_text),
                    sum(1 for e in projection.events if e.get("type") == "tool"),
                )

        return StreamingResponse(gen(), media_type="text/event-stream", headers=sse_headers)

    return router


__all__ = [
    "AssistantSessionStore",
    "PLAN_PROMPT_ADDENDUM",
    "EXPORT_NOTICE",
    "build_assistant_sessions_router",
    "export_markdown",
    "resolve_sessions_db_path",
]
