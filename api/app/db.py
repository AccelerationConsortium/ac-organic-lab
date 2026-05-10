"""
SQLite database manager for lab observability.

Stores equipment uptime events, equipment state-change events, environmental
sensor readings (downsampled), dosing run records, and per-well results.

All writes use the stdlib ``sqlite3`` module (no extra dependencies).
Reads/writes that happen on the asyncio event loop go through
``asyncio.get_event_loop().run_in_executor(None, ...)`` so the async
endpoints never block.  The background poll task is a plain coroutine that
calls the synchronous write helpers directly (it sleeps between iterations
so one blocking sqlite3 call of a few milliseconds is fine).

Database location
-----------------
Resolved in this order:

1. ``LAB_DB_PATH`` environment variable
2. ``../data/lab.db`` relative to this file  (i.e. repo-root ``data/``)

Add ``ReadWritePaths=/opt/ac-organic-dashboard/data`` to the systemd unit
if you change the default, or point ``LAB_DB_PATH`` to a writable path.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Equipment state-change events (startup, shutdown, error, state_transition, …)
-- Written by the background poll loop when state changes, and by the
-- /api/ingest/events endpoint when a device posts its own events.jsonl records.
CREATE TABLE IF NOT EXISTS equipment_events (
    id          INTEGER PRIMARY KEY,
    ts          TEXT    NOT NULL,
    device_id   TEXT    NOT NULL,
    event_type  TEXT    NOT NULL,
    from_state  TEXT,
    to_state    TEXT,
    message     TEXT,
    payload     TEXT                -- JSON blob for extra fields
);
CREATE INDEX IF NOT EXISTS idx_ee_device_ts ON equipment_events(device_id, ts);

-- Service reachability events (up / down / unreachable / recovered).
-- One row on every transition.  Used to compute uptime % over a window.
CREATE TABLE IF NOT EXISTS service_uptime (
    id                   INTEGER PRIMARY KEY,
    ts                   TEXT    NOT NULL,
    device_id            TEXT    NOT NULL,
    event                TEXT    NOT NULL
                                 CHECK(event IN ('up','down','unreachable','recovered')),
    consecutive_failures INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_su_device_ts ON service_uptime(device_id, ts);

-- Environmental sensor readings (downsampled — at most one row per minute).
CREATE TABLE IF NOT EXISTS sensor_readings (
    id        INTEGER PRIMARY KEY,
    ts        TEXT    NOT NULL,
    sensor_id TEXT    NOT NULL,
    metric    TEXT    NOT NULL,   -- 'temperature_c' | 'humidity_pct' | 'co2_ppm' | …
    value     REAL    NOT NULL,
    unit      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sr_sensor_ts ON sensor_readings(sensor_id, ts);

-- Dosing runs (one row per 96-well plate attempt).
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT    PRIMARY KEY,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    device_id     TEXT    NOT NULL,
    config_name   TEXT,
    plate_id      TEXT,
    compound_id   TEXT,
    target_mg     REAL,
    n_wells       INTEGER DEFAULT 0,
    n_converged   INTEGER DEFAULT 0,
    status        TEXT    DEFAULT 'in_progress'
                          CHECK(status IN ('in_progress','complete','failed','aborted'))
);

-- Per-well dispense results.
CREATE TABLE IF NOT EXISTS well_results (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT    REFERENCES runs(id) ON DELETE CASCADE,
    ts          TEXT    NOT NULL,
    well        TEXT    NOT NULL,
    target_mg   REAL    NOT NULL,
    actual_mg   REAL,
    converged   INTEGER NOT NULL DEFAULT 0,
    iterations  INTEGER,
    duration_s  REAL
);
CREATE INDEX IF NOT EXISTS idx_wr_run ON well_results(run_id);
"""


# ---------------------------------------------------------------------------
# LabDatabase
# ---------------------------------------------------------------------------


class LabDatabase:
    """Thread-safe SQLite database manager.

    Open once at application startup via :meth:`open`.  Call :meth:`close`
    on shutdown.  All methods are synchronous and safe to call from any
    thread.  Wrap with ``run_in_executor`` for async callers.
    """

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------ lifecycle

    def open(self) -> None:
        """Open (or create) the database and apply the schema."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            timeout=10,
        )
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn
        logger.info("Lab database open: %s", self._path)

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        assert self._conn is not None, "Database not open"
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        assert self._conn is not None, "Database not open"
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------------ writes

    def record_uptime_event(
        self,
        device_id: str,
        event: str,
        *,
        ts: Optional[str] = None,
        consecutive_failures: int = 0,
    ) -> None:
        self._execute(
            "INSERT INTO service_uptime(ts, device_id, event, consecutive_failures)"
            " VALUES (?, ?, ?, ?)",
            (ts or _now(), device_id, event, consecutive_failures),
        )

    def record_equipment_event(
        self,
        device_id: str,
        event_type: str,
        *,
        ts: Optional[str] = None,
        from_state: Optional[str] = None,
        to_state: Optional[str] = None,
        message: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        self._execute(
            "INSERT INTO equipment_events"
            "(ts, device_id, event_type, from_state, to_state, message, payload)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ts or _now(),
                device_id,
                event_type,
                from_state,
                to_state,
                message,
                json.dumps(payload) if payload else None,
            ),
        )

    def record_sensor_reading(
        self,
        sensor_id: str,
        metric: str,
        value: float,
        unit: str,
        *,
        ts: Optional[str] = None,
    ) -> None:
        self._execute(
            "INSERT INTO sensor_readings(ts, sensor_id, metric, value, unit)"
            " VALUES (?, ?, ?, ?, ?)",
            (ts or _now(), sensor_id, metric, value, unit),
        )

    def upsert_run(self, run: dict[str, Any]) -> None:
        """Insert or update a run record."""
        self._execute(
            """
            INSERT INTO runs(id, started_at, finished_at, device_id, config_name,
                             plate_id, compound_id, target_mg, n_wells, n_converged, status)
            VALUES (:id, :started_at, :finished_at, :device_id, :config_name,
                    :plate_id, :compound_id, :target_mg, :n_wells, :n_converged, :status)
            ON CONFLICT(id) DO UPDATE SET
                finished_at  = excluded.finished_at,
                n_wells      = excluded.n_wells,
                n_converged  = excluded.n_converged,
                status       = excluded.status
            """,
            run,
        )

    def insert_well_result(self, result: dict[str, Any]) -> None:
        self._execute(
            """
            INSERT INTO well_results(run_id, ts, well, target_mg, actual_mg,
                                     converged, iterations, duration_s)
            VALUES (:run_id, :ts, :well, :target_mg, :actual_mg,
                    :converged, :iterations, :duration_s)
            """,
            result,
        )

    # ------------------------------------------------------------------ queries

    def get_uptime_pct(self, device_id: str, *, days: int = 7) -> float:
        """Return uptime percentage (0–100) over the last *days* days.

        Uses leading-edge timing: each 'up' or 'down' event is charged until
        the next event (or now, for the most recent event).
        """
        window_s = days * 86400
        rows = self._fetchall(
            """
            SELECT ts, event,
                   LEAD(ts) OVER (ORDER BY ts) AS next_ts
            FROM service_uptime
            WHERE device_id = ?
              AND ts >= datetime('now', ? || ' days')
            ORDER BY ts
            """,
            (device_id, f"-{days}"),
        )
        if not rows:
            return 0.0

        up_seconds = 0.0
        for row in rows:
            if row["event"] not in ("up", "recovered"):
                continue
            t0 = _parse_ts(row["ts"])
            t1 = _parse_ts(row["next_ts"]) if row["next_ts"] else datetime.now(timezone.utc)
            up_seconds += (t1 - t0).total_seconds()

        return round(min(100.0, up_seconds / window_s * 100), 1)

    def get_uptime_events(
        self, device_id: str, *, limit: int = 200
    ) -> list[dict]:
        rows = self._fetchall(
            "SELECT ts, event, consecutive_failures FROM service_uptime"
            " WHERE device_id = ? ORDER BY ts DESC LIMIT ?",
            (device_id, limit),
        )
        return [dict(r) for r in rows]

    def get_equipment_events(
        self, device_id: str, *, limit: int = 50
    ) -> list[dict]:
        rows = self._fetchall(
            "SELECT ts, event_type, from_state, to_state, message, payload"
            " FROM equipment_events WHERE device_id = ?"
            " ORDER BY ts DESC LIMIT ?",
            (device_id, limit),
        )
        result = []
        for r in rows:
            d = dict(r)
            if d["payload"]:
                d["payload"] = json.loads(d["payload"])
            result.append(d)
        return result

    def get_sensor_readings(
        self,
        sensor_id: str,
        metric: str,
        *,
        since_hours: float = 1.0,
        limit: int = 200,
    ) -> list[dict]:
        rows = self._fetchall(
            "SELECT ts, value, unit FROM sensor_readings"
            " WHERE sensor_id = ? AND metric = ?"
            "   AND ts >= datetime('now', ? || ' hours')"
            " ORDER BY ts ASC LIMIT ?",
            (sensor_id, metric, f"-{since_hours}", limit),
        )
        return [dict(r) for r in rows]

    def get_runs(self, *, limit: int = 20, device_id: Optional[str] = None) -> list[dict]:
        if device_id:
            rows = self._fetchall(
                "SELECT * FROM runs WHERE device_id = ?"
                " ORDER BY started_at DESC LIMIT ?",
                (device_id, limit),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in rows]

    def get_well_results(self, run_id: str) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM well_results WHERE run_id = ? ORDER BY well",
            (run_id,),
        )
        return [dict(r) for r in rows]

    def get_latest_sensor_values(self) -> list[dict]:
        """Most recent reading per (sensor_id, metric) pair — for live tiles."""
        rows = self._fetchall(
            """
            SELECT sensor_id, metric, value, unit, ts
            FROM sensor_readings
            WHERE id IN (
                SELECT MAX(id) FROM sensor_readings GROUP BY sensor_id, metric
            )
            ORDER BY sensor_id, metric
            """,
        )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def resolve_db_path() -> Path:
    """Return the database path from env or the default location."""
    env = os.environ.get("LAB_DB_PATH")
    if env:
        return Path(env)
    # Default: repo-root/data/lab.db
    return Path(__file__).resolve().parents[3] / "data" / "lab.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        # sqlite LEAD() produces naive strings like '2026-05-10 18:00:00'
        return datetime.fromisoformat(ts.replace(" ", "T")).replace(tzinfo=timezone.utc)
