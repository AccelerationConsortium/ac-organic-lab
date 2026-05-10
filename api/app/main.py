"""FastAPI entry point for the dashboard.

Imports the registry, polling aggregator, and per-device adapters from the
``lab-skills`` SDK; composes the SDK's snapshots with dashboard
presentation fields (``tile``, ``location``) plus the registry's
``enabled`` / ``maintenance`` mirrors before returning them on
``/api/equipment`` and ``/api/equipment/{id}/status``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from lab_skills import EquipmentAggregator, load_registry
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .control import build_control_router
from .db import LabDatabase, resolve_db_path
from .history import build_history_router
from .presentation import (
    AggregatorHealth,
    EquipmentList,
    EquipmentSnapshot,
    _snapshot,
    compose_equipment_list,
    load_dashboard_overrides,
)

logger = logging.getLogger("ac_dashboard.api")


def _cors_origins() -> list[str]:
    raw = os.environ.get("DASHBOARD_CORS_ORIGINS", "http://localhost:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# ---------------------------------------------------------------------------
# Background uptime poll task
# ---------------------------------------------------------------------------

# Tracks last known reachability per device_id across poll iterations.
_last_reachable: dict[str, bool] = {}
_consecutive_failures: dict[str, int] = {}


async def _uptime_poll_loop(aggregator: EquipmentAggregator, db: LabDatabase) -> None:
    """Poll all devices every 60 s and write uptime transition events to SQLite.

    Only writes a row when reachability *changes* — not every poll — so the
    table stays small and the uptime query stays fast.

    Also writes sensor readings for any device whose status carries sensor
    metrics in ``status.details`` (once the env_sensors service is live).
    """
    # Give the aggregator a moment to complete its first poll cycle.
    await asyncio.sleep(5)

    while True:
        try:
            skill_list = await aggregator.fetch_all()
            for snap in skill_list.equipment:
                device_id = snap.id
                reachable = snap.fetch_error is None

                prev = _last_reachable.get(device_id)

                if prev is None:
                    # First observation — record initial state, no transition row.
                    _last_reachable[device_id] = reachable
                    _consecutive_failures[device_id] = 0
                    continue

                if reachable and not prev:
                    # Came back up.
                    _consecutive_failures[device_id] = 0
                    db.record_uptime_event(device_id, "recovered")
                    db.record_equipment_event(
                        device_id,
                        "state_transition",
                        from_state="unreachable",
                        to_state=_state_str(snap),
                        message="Device recovered",
                    )
                    logger.info("Uptime: %s recovered", device_id)
                    _last_reachable[device_id] = True

                elif not reachable and prev:
                    # Just went down.
                    _consecutive_failures[device_id] = 1
                    db.record_uptime_event(device_id, "down", consecutive_failures=1)
                    db.record_equipment_event(
                        device_id,
                        "state_transition",
                        from_state=_state_str(snap),
                        to_state="unreachable",
                        message=str(snap.fetch_error),
                    )
                    logger.warning("Uptime: %s went DOWN — %s", device_id, snap.fetch_error)
                    _last_reachable[device_id] = False

                elif not reachable:
                    # Still down — increment failure counter, update last row.
                    _consecutive_failures[device_id] = _consecutive_failures.get(device_id, 0) + 1

                # Sensor readings — only written when the device exposes them.
                # Remove the ``continue`` guard below once env_sensors is live.
                _write_sensor_readings(db, snap)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Uptime poll error: %s", exc)

        await asyncio.sleep(60)


def _state_str(snap) -> str:
    """Extract a state string from an SDK snapshot (best-effort)."""
    try:
        return snap.status.state or "unknown"
    except Exception:
        return "unknown"


def _write_sensor_readings(db: LabDatabase, snap) -> None:
    """Write sensor metrics from a device snapshot (no-op if none present)."""
    try:
        details = snap.status.details
        if details is None:
            return
        # When env_sensors exposes readings, they will appear as top-level
        # fields on the details dict under keys like 'temperature_c',
        # 'humidity_pct', 'co2_ppm'.  Adjust the key list when the device
        # contract is finalised.
        metric_map = {
            "temperature_c": "°C",
            "humidity_pct": "%",
            "co2_ppm": "ppm",
            "pressure_hpa": "hPa",
        }
        details_dict = details.model_dump() if hasattr(details, "model_dump") else {}
        for metric, unit in metric_map.items():
            value = details_dict.get(metric)
            if value is not None:
                db.record_sensor_reading(snap.id, metric, float(value), unit)
    except Exception:
        pass  # sensor data is best-effort; never crash the poll loop


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Equipment registry + aggregator
    registry = load_registry()
    aggregator = EquipmentAggregator(registry)
    await aggregator.startup()
    app.state.aggregator = aggregator
    app.state.registry = registry
    app.state.overrides = load_dashboard_overrides()
    logger.info("Loaded equipment registry: %d entries", aggregator.equipment_count)

    # Lab history database
    db_path = resolve_db_path()
    db = LabDatabase(db_path)
    try:
        db.open()
    except Exception as exc:
        logger.error("Could not open lab database at %s: %s — history endpoints disabled", db_path, exc)
        db = None  # type: ignore[assignment]
    app.state.db = db

    # Background uptime poll
    poll_task = None
    if db is not None:
        poll_task = asyncio.create_task(_uptime_poll_loop(aggregator, db))

    try:
        yield
    finally:
        if poll_task is not None:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
        await aggregator.shutdown()
        if db is not None:
            db.close()


app = FastAPI(
    title="AC Organic Self-driving Lab Dashboard API",
    description=(
        "Aggregates lab-equipment status into one normalized contract. "
        "See docs/STATUS_SPEC.md for the equipment-side contract."
    ),
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# Mutating control surface (PTZ, presets, plug on/off, ...). Forwards to the
# device gateway named by ``equipment.yaml::base_url``. See
# ``api/app/control.py`` for the routing rules.
app.include_router(build_control_router())
# History + ingest endpoints (SQLite-backed).
app.include_router(build_history_router())


def _aggregator() -> EquipmentAggregator:
    aggregator: EquipmentAggregator | None = getattr(app.state, "aggregator", None)
    if aggregator is None:
        raise HTTPException(status_code=503, detail="Aggregator not initialized")
    return aggregator


@app.get("/api/health", response_model=AggregatorHealth, tags=["meta"])
async def health() -> AggregatorHealth:
    aggregator = _aggregator()
    return AggregatorHealth(
        version=__version__,
        equipment_count=aggregator.equipment_count,
    )


@app.get("/api/equipment", response_model=EquipmentList, tags=["equipment"])
async def list_equipment() -> EquipmentList:
    """Return the latest status of every registered equipment in parallel."""

    aggregator = _aggregator()
    skill_list = await aggregator.fetch_all()
    return compose_equipment_list(
        skill_list,
        app.state.overrides,
        app.state.registry,
    )


@app.get(
    "/api/equipment/{equipment_id}/status",
    response_model=EquipmentSnapshot,
    tags=["equipment"],
)
async def get_equipment(equipment_id: str) -> EquipmentSnapshot:
    """Live status fetch for a single equipment."""

    aggregator = _aggregator()
    sdk_snapshot = await aggregator.fetch_one(equipment_id)
    if sdk_snapshot is None:
        raise HTTPException(status_code=404, detail=f"Unknown equipment id: {equipment_id}")
    override = app.state.overrides.get(equipment_id)
    return _snapshot(sdk_snapshot, override, app.state.registry)
