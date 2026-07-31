"""
History and ingest API endpoints.

History endpoints (read-only, for the dashboard)
-------------------------------------------------
GET  /api/history/uptime                        Uptime % for all devices (bulk)
GET  /api/history/uptime/{device_id}            Uptime % + event list for one device
GET  /api/history/events/{device_id}            Equipment events (startup, errors, …)
GET  /api/history/sensors/latest                Latest value per sensor/metric (live tile)
GET  /api/history/sensors/{sensor_id}/{metric}  Downsampled readings for one metric
GET  /api/history/runs                          Recent dosing runs
GET  /api/history/runs/{run_id}/wells           Per-well results for one run

Ingest endpoints (written to by device services or workflow scripts)
--------------------------------------------------------------------
POST /api/ingest/events       Batch-accept events.jsonl records from a device
POST /api/ingest/runs         Create or update a run record
POST /api/ingest/wells        Append per-well results to a run

All SQLite calls are dispatched via ``run_in_executor`` so the async event loop
is never blocked.  See ``db.py`` for schema and ``LAB_MONITORING.md`` for design
rationale, direct-query examples, and retention guidelines.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .events import ACTIVITY_TRANSITION

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class UptimeResponse(BaseModel):
    device_id: str
    days: int
    uptime_pct: float
    events: list[dict]


class SensorReadingPoint(BaseModel):
    ts: str
    value: float
    unit: str


class SensorHistoryResponse(BaseModel):
    sensor_id: str
    metric: str
    since_hours: float
    readings: list[SensorReadingPoint]


class RunRecord(BaseModel):
    id: str
    started_at: str
    finished_at: Optional[str] = None
    device_id: str
    config_name: Optional[str] = None
    plate_id: Optional[str] = None
    compound_id: Optional[str] = None
    target_mg: Optional[float] = None
    n_wells: int = 0
    n_converged: int = 0
    status: str = "in_progress"


class WellResultRecord(BaseModel):
    run_id: str
    ts: str
    well: str
    target_mg: float
    actual_mg: Optional[float] = None
    converged: bool = False
    iterations: Optional[int] = None
    duration_s: Optional[float] = None


class IngestEventRecord(BaseModel):
    """One record from a device's events.jsonl."""
    timestamp: str
    event: str
    # Optional typed fields — present depending on event type
    config_name: Optional[str] = None
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    message: Optional[str] = None
    context: Optional[str] = None
    # Everything else lands in the payload blob
    extra: dict[str, Any] = Field(default_factory=dict)


class IngestEventsRequest(BaseModel):
    device_id: str
    records: list[IngestEventRecord]


# ---------------------------------------------------------------------------
# Router builder
# ---------------------------------------------------------------------------


def build_history_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["history"])

    def _db(request: Request):
        db = getattr(request.app.state, "db", None)
        if db is None:
            raise HTTPException(status_code=503, detail="Database not available")
        return db

    # ------------------------------------------------------------------ uptime

    @router.get("/history/uptime/{device_id}", response_model=UptimeResponse)
    async def uptime(
        device_id: str,
        days: int = 7,
        request: Request = ...,
    ):
        """Equipment uptime percentage + raw transition events for the last N days.

        Use this to render a per-device uptime tile or a 7-day uptime bar.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        db = _db(request)
        pct = await loop.run_in_executor(None, lambda: db.get_uptime_pct(device_id, days=days))
        events = await loop.run_in_executor(None, lambda: db.get_uptime_events(device_id))
        return UptimeResponse(device_id=device_id, days=days, uptime_pct=pct, events=events)

    @router.get("/history/uptime")
    async def uptime_all(days: int = 7, request: Request = ...):
        """Uptime summary for every device in one request.

        Returns ``{device_id: {uptime_pct, last_event, state_pcts,
        activity_pcts, activity_tracking_since, days}}`` — suitable for the
        dashboard uptime overview table.  Devices with no uptime data yet
        return ``uptime_pct: null``.

        ``activity_pcts`` (idle/running/unknown, STATUS_SPEC v1.2 §2.3) is a
        60 s poll-sampled series: operations shorter than the poll interval
        are missed outright, so per §2.3.1 it must be rendered as sampled
        observation — never as usage accounting — alongside
        ``activity_tracking_since`` (null until the first activity row).
        """
        import asyncio
        loop = asyncio.get_event_loop()
        db = _db(request)

        # Discover which device IDs have at least one uptime row.
        rows = await loop.run_in_executor(
            None,
            lambda: db._fetchall(
                "SELECT DISTINCT device_id FROM service_uptime"
            ),
        )
        device_ids = [r["device_id"] for r in rows]

        results = {}
        for did in device_ids:
            pct = await loop.run_in_executor(
                None, lambda d=did: db.get_uptime_pct(d, days=days)
            )
            last = await loop.run_in_executor(
                None,
                lambda d=did: db._fetchall(
                    "SELECT ts, event FROM service_uptime"
                    " WHERE device_id = ? ORDER BY ts DESC LIMIT 1",
                    (d,),
                ),
            )
            state_pcts = await loop.run_in_executor(
                None, lambda d=did: db.get_state_time_pcts(d, days=days)
            )
            activity_pcts = await loop.run_in_executor(
                None, lambda d=did: db.get_activity_time_pcts(d, days=days)
            )
            activity_since = await loop.run_in_executor(
                None,
                lambda d=did: db.get_first_event_ts(d, ACTIVITY_TRANSITION),
            )
            cycles = await loop.run_in_executor(
                None, lambda d=did: db.get_cycle_count(d, days=days)
            )
            results[did] = {
                "device_id": did,
                "days": days,
                "uptime_pct": pct,
                "last_event": dict(last[0]) if last else None,
                "state_pcts": state_pcts,
                "activity_pcts": activity_pcts,
                "activity_tracking_since": activity_since,
                # Exact completed-cycle count from metrics["cycles_total"]
                # (§2.3.1) — null when the device doesn't publish the counter.
                # Unlike activity_pcts this survives sub-poll-interval cycles.
                "cycles": cycles,
            }

        return {"devices": results, "days": days}

    # ------------------------------------------------------------------ equipment events

    @router.get("/history/events/{device_id}")
    async def equipment_events(
        device_id: str,
        limit: int = 50,
        event_type: str | None = None,
        request: Request = ...,
    ):
        """State transitions, errors, startup/shutdown events for one device.

        Pass ``event_type`` to narrow to one kind — e.g.
        ``event_type=agent_observation`` to read back prior agent findings
        (what PyPoe's investigator journals via ``append_observation``).
        """
        import asyncio
        loop = asyncio.get_event_loop()
        db = _db(request)
        rows = await loop.run_in_executor(
            None,
            lambda: db.get_equipment_events(
                device_id, limit=limit, event_type=event_type
            ),
        )
        return {"device_id": device_id, "events": rows}

    @router.get("/history/control-actions")
    async def control_actions(
        limit: int = 100,
        device_id: str | None = None,
        request: Request = ...,
    ):
        """Operator control-write audit feed across all devices — one row per
        dashboard-mediated `/control/*` call (actor, action, outcome). Backs
        the admin page's audit panel."""
        import asyncio
        loop = asyncio.get_event_loop()
        db = _db(request)
        rows = await loop.run_in_executor(
            None,
            lambda: db.get_control_actions(limit=min(limit, 500), device_id=device_id),
        )
        return {"actions": rows}

    # ------------------------------------------------------------------ sensors

    @router.get("/history/sensors/latest")
    async def sensors_latest(request: Request = ...):
        """Most recent reading per (sensor_id, metric) — for live tiles.

        Returns a flat list:
        ``[{sensor_id, metric, value, unit, ts}, …]``
        """
        import asyncio
        loop = asyncio.get_event_loop()
        db = _db(request)
        rows = await loop.run_in_executor(None, db.get_latest_sensor_values)
        return {"readings": rows}

    @router.get(
        "/history/sensors/{sensor_id}/{metric}",
        response_model=SensorHistoryResponse,
    )
    async def sensor_history(
        sensor_id: str,
        metric: str,
        since_hours: float = 1.0,
        limit: int = 2000,
        request: Request = ...,
    ):
        """Downsampled readings for one metric over the last N hours.

        Typical use: line chart on the environmental monitoring panel or the
        Switches power-consumption tile.

        At 60 s poll intervals a 7-day window produces up to 10 080 points;
        the default cap of 2 000 naturally downsamples that to ~1 point per
        ~5 minutes, which is enough resolution for a sparkline.

        Common metric names: ``temperature``, ``humidity``, ``voc``, ``nox``,
        ``pm25``, ``pm10``, ``battery``, ``power_outlet_N``,
        ``energy_kwh_cumul_outlet_N``. Environmental names carry no unit suffix
        (the unit is a column); rows written before 2026-07-31 used
        ``temperature_c`` / ``humidity_pct`` / ``co2_ppm`` and are synthetic
        mock data left in place but no longer queried.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        db = _db(request)
        rows = await loop.run_in_executor(
            None,
            lambda: db.get_sensor_readings(
                sensor_id, metric, since_hours=since_hours, limit=limit
            ),
        )
        return SensorHistoryResponse(
            sensor_id=sensor_id,
            metric=metric,
            since_hours=since_hours,
            readings=[SensorReadingPoint(**r) for r in rows],
        )

    # ------------------------------------------------------------------ runs

    @router.get("/history/runs")
    async def list_runs(
        limit: int = 20,
        device_id: Optional[str] = None,
        request: Request = ...,
    ):
        """Most recent dosing runs, newest first.

        Use this to render a run history table or a per-plate success rate tile.
        Filter to one device with ``?device_id=dose_every_well``.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        db = _db(request)
        runs = await loop.run_in_executor(
            None, lambda: db.get_runs(limit=limit, device_id=device_id)
        )
        return {"runs": runs}

    @router.get("/history/runs/{run_id}/wells")
    async def get_well_results(run_id: str, request: Request = ...):
        """Per-well dispense results for one run.

        Returns 96 rows for a full plate.  Use this to render a 96-well
        heatmap coloured by ``actual_mg`` or ``converged``.

        Example heatmap colour scale:
          - grey  → well not dosed
          - green → converged, actual_mg within 5% of target
          - amber → converged, actual_mg 5–15% off
          - red   → not converged
        """
        import asyncio
        loop = asyncio.get_event_loop()
        db = _db(request)
        wells = await loop.run_in_executor(
            None, lambda: db.get_well_results(run_id)
        )
        if not wells:
            raise HTTPException(
                status_code=404, detail=f"No well results found for run {run_id!r}"
            )
        return {"run_id": run_id, "wells": wells}

    # ------------------------------------------------------------------ ingest

    @router.post("/ingest/events", status_code=204)
    async def ingest_events(body: IngestEventsRequest, request: Request = ...):
        """Receive events.jsonl records POSTed by a device service.

        The device-side exporter tails its own ``events.jsonl`` and POSTs
        batches here.  The aggregator stores them in ``equipment_events``.

        Idempotent for duplicate timestamps per device — duplicates are
        silently ignored (SQLite unique index on ts+device_id is optional;
        for now just insert and accept occasional duplicates).
        """
        import asyncio
        loop = asyncio.get_event_loop()
        db = _db(request)

        def _write():
            for rec in body.records:
                # Build extra payload from any unrecognised fields
                payload: dict[str, Any] = {}
                for key in (
                    "config_name", "owner", "token_prefix", "ttl_s",
                    "well", "target_mg", "actual_mg", "converged",
                    "iterations", "duration_s", "flow_rate_mg_s",
                ):
                    val = getattr(rec, key, None) or rec.extra.get(key)
                    if val is not None:
                        payload[key] = val
                if rec.extra:
                    payload.update(rec.extra)

                db.record_equipment_event(
                    device_id=body.device_id,
                    event_type=rec.event,
                    ts=rec.timestamp,
                    from_state=rec.from_state,
                    to_state=rec.to_state,
                    message=rec.message or rec.context,
                    payload=payload or None,
                )

        await loop.run_in_executor(None, _write)
        logger.debug("Ingested %d events from %s", len(body.records), body.device_id)

    @router.post("/ingest/runs", status_code=204)
    async def ingest_run(body: RunRecord, request: Request = ...):
        """Create or update a run record.

        Call this once when a run starts (``status=in_progress``) and again
        when it finishes (``status=complete|failed|aborted``).
        """
        import asyncio
        loop = asyncio.get_event_loop()
        db = _db(request)
        await loop.run_in_executor(None, lambda: db.upsert_run(body.model_dump()))

    @router.post("/ingest/wells", status_code=204)
    async def ingest_wells(
        wells: list[WellResultRecord],
        request: Request = ...,
    ):
        """Append per-well results to an existing run.

        Can be called once per well (streaming) or in bulk at the end of a row.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        db = _db(request)

        def _write():
            for w in wells:
                db.insert_well_result({
                    **w.model_dump(),
                    "converged": int(w.converged),
                })

        await loop.run_in_executor(None, _write)

    return router
