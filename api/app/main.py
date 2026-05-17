"""FastAPI entry point for the dashboard.

Imports the registry, polling aggregator, and per-device adapters from the
``lab-skills`` SDK; composes the SDK's snapshots with dashboard
presentation fields (``tile``, ``location``) plus the registry's
``enabled`` / ``maintenance`` mirrors before returning them on
``/api/equipment`` and ``/api/equipment/{id}/status``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from lab_skills import EquipmentAggregator, load_registry
from lab_skills.skill_catalog import SKILL_REGISTRY
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

# Tracks last known reachability + equipment state per device_id across polls.
_last_reachable: dict[str, bool] = {}
_consecutive_failures: dict[str, int] = {}
_last_state: dict[str, str] = {}

# Per-outlet energy accumulator for power strips/smart plugs.
# Maps device_id → {metric_key → last_seen_today_value}.
# Seeded on the first observation; used to compute cumulative kWh across
# midnight resets (energy_kwh_today resets to 0 at midnight on the device).
_plug_energy_accum: dict[str, dict[str, float]] = {}


async def _uptime_poll_loop(aggregator: EquipmentAggregator, db: LabDatabase, registry) -> None:
    """Poll all devices every 60 s and write uptime transition events to SQLite.

    Only writes a new transition row when reachability *changes* (plus one row
    on the very first observation so the uptime table is populated immediately).

    Also writes sensor readings for any device whose status carries sensor
    metrics in ``status.details``.  For mock environmental sensors, generates
    synthetic readings so the History → Sensors tab is useful before real
    hardware is deployed.
    """
    # Build a lookup map from the registry for adapter/kind checks.
    _registry_map = {
        entry.id: entry
        for entry in dict(registry).get("equipment", [])
    }

    # Give the aggregator a moment to complete its first poll cycle.
    await asyncio.sleep(5)

    while True:
        try:
            skill_list = await aggregator.fetch_all()
            for snap in skill_list.equipment:
                device_id = snap.id
                reachable = snap.fetch_error is None

                prev = _last_reachable.get(device_id)
                current_state = _state_str(snap)

                if prev is None:
                    # First observation — write an initial row so the uptime
                    # table is populated right away, then carry on to also
                    # capture sensor readings below.
                    _last_reachable[device_id] = reachable
                    _consecutive_failures[device_id] = 0
                    initial_event = "up" if reachable else "down"
                    db.record_uptime_event(device_id, initial_event)
                    db.record_equipment_event(
                        device_id,
                        "state_transition",
                        from_state=None,
                        to_state=current_state,
                        message="Initial observation",
                    )
                    _last_state[device_id] = current_state
                    logger.info(
                        "Uptime: %s initial → %s (%s)",
                        device_id, initial_event, current_state,
                    )
                    # Fall through to sensor reads below (no continue).

                elif reachable and not prev:
                    # Came back up.
                    _consecutive_failures[device_id] = 0
                    db.record_uptime_event(device_id, "recovered")
                    db.record_equipment_event(
                        device_id,
                        "state_transition",
                        from_state="unreachable",
                        to_state=current_state,
                        message="Device recovered",
                    )
                    _last_state[device_id] = current_state
                    logger.info("Uptime: %s recovered → %s", device_id, current_state)
                    _last_reachable[device_id] = True

                elif not reachable and prev:
                    # Just went down.
                    _consecutive_failures[device_id] = 1
                    db.record_uptime_event(device_id, "down", consecutive_failures=1)
                    db.record_equipment_event(
                        device_id,
                        "state_transition",
                        from_state=_last_state.get(device_id, "unknown"),
                        to_state="unreachable",
                        message=str(snap.fetch_error),
                    )
                    _last_state[device_id] = "unreachable"
                    logger.warning("Uptime: %s went DOWN — %s", device_id, snap.fetch_error)
                    _last_reachable[device_id] = False

                elif not reachable:
                    # Still down — increment failure counter.
                    _consecutive_failures[device_id] = _consecutive_failures.get(device_id, 0) + 1

                else:
                    # Reachable and previously reachable — check for state change
                    # within the equipment's own state machine (e.g. ready → busy).
                    prev_state = _last_state.get(device_id)
                    if prev_state is not None and prev_state != current_state:
                        db.record_equipment_event(
                            device_id,
                            "state_transition",
                            from_state=prev_state,
                            to_state=current_state,
                            message="Equipment state changed",
                        )
                        logger.info(
                            "State: %s %s → %s", device_id, prev_state, current_state,
                        )
                        _last_state[device_id] = current_state
                    elif prev_state is None:
                        _last_state[device_id] = current_state

                # Sensor readings — real devices expose them in status.details;
                # mock environmental sensors get synthetic readings generated here.
                reg_entry = _registry_map.get(device_id)
                if (
                    reg_entry is not None
                    and reg_entry.kind == "environmental_sensor"
                    and reg_entry.adapter == "mock"
                ):
                    _write_mock_sensor_readings(db, device_id)
                elif (
                    reg_entry is not None
                    and reg_entry.kind in ("smart_plug", "power_strip")
                ):
                    _write_plug_readings(db, snap)
                else:
                    _write_sensor_readings(db, snap)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Uptime poll error: %s", exc)

        # Prune old sensor readings once per cycle to bound table growth.
        try:
            db.prune_sensor_readings(keep_days=30)
        except Exception:
            pass

        await asyncio.sleep(60)


def _state_str(snap) -> str:
    """Extract a state string from an SDK snapshot (best-effort).

    The SDK exposes the runtime state on ``snap.status.equipment_status``
    (matches docs/STATUS_SPEC.md).  ``snap.fetch_error`` overrides everything
    when the aggregator cannot reach the device.
    """
    try:
        if snap.fetch_error is not None:
            return "unreachable"
        return snap.status.equipment_status or "unknown"
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


def _write_plug_readings(db: LabDatabase, snap) -> None:
    """Write per-outlet power and energy metrics from a plug/power-strip snapshot.

    Records three metric families into ``sensor_readings``:
    - ``power_outlet_N``            (W)   — instantaneous draw
    - ``current_outlet_N``          (A)   — instantaneous current
    - ``energy_kwh_today_outlet_N`` (kWh) — device's own daily counter (resets at midnight)

    Additionally maintains a running cumulative counter
    ``energy_kwh_cumul_outlet_N`` (kWh) that survives midnight resets.  On the
    first observation after an API restart the accumulator is seeded without
    writing, so we never double-count today's usage.
    """
    try:
        metrics = getattr(snap.status, "metrics", None) or {}

        _PREFIX_UNIT = {
            "power_outlet_":            "W",
            "energy_kwh_today_outlet_": "kWh",
            "current_outlet_":          "A",
        }

        device_accum = _plug_energy_accum.setdefault(snap.id, {})

        for key, entry in metrics.items():
            raw = entry.value if hasattr(entry, "value") else entry
            if raw is None:
                continue
            value = float(raw)

            # Store the raw metric for whichever family it belongs to.
            for prefix, unit in _PREFIX_UNIT.items():
                if key.startswith(prefix):
                    db.record_sensor_reading(snap.id, key, value, unit)
                    break

            # Cumulative energy accumulation for daily-resetting counters.
            if key.startswith("energy_kwh_today_outlet_"):
                if key not in device_accum:
                    # First observation after startup — seed the accumulator.
                    # Don't write a delta yet; we have no reference point.
                    device_accum[key] = value
                else:
                    prev = device_accum[key]
                    # A decrease means midnight reset — treat the new reading
                    # as the full delta since midnight.
                    delta = value if value < prev else (value - prev)
                    if delta > 0:
                        outlet_idx = key[len("energy_kwh_today_outlet_"):]
                        cumul_key  = f"energy_kwh_cumul_outlet_{outlet_idx}"
                        last_cumul = db.get_last_sensor_value(snap.id, cumul_key) or 0.0
                        db.record_sensor_reading(snap.id, cumul_key, last_cumul + delta, "kWh")
                    device_accum[key] = value

    except Exception:
        pass  # never crash the poll loop


def _mock_sensor_phase(sensor_id: str) -> float:
    """Deterministic phase offset (0..2π) derived from a sensor's ID."""
    h = int(hashlib.md5(sensor_id.encode()).hexdigest()[:8], 16)
    return (h % 10000) / 10000.0 * 2 * math.pi


def _write_mock_sensor_readings(db: LabDatabase, sensor_id: str) -> None:
    """Generate plausible synthetic readings for a mock environmental sensor.

    Values drift slowly on independent sine waves so different sensors show
    distinct but realistic trends.  Baselines match a typical indoor lab:
      temperature  21.5 ± 1.5 °C
      humidity     48 ± 6 %
      co2          480 ± 90 ppm
    """
    try:
        t = time.time()
        φ = _mock_sensor_phase(sensor_id)

        temp = 21.5 + 1.5 * math.sin(t / 3600 + φ) + 0.3 * math.sin(t / 600 + φ * 2)
        hum  = 48.0 + 6.0 * math.sin(t / 7200 + φ + 1.0) + 1.2 * math.sin(t / 900)
        co2  = 480.0 + 90.0 * math.sin(t / 14400 + φ + 2.0) + 25.0 * math.sin(t / 1800)

        db.record_sensor_reading(sensor_id, "temperature_c", round(temp, 1), "°C")
        db.record_sensor_reading(sensor_id, "humidity_pct",  round(hum,  1), "%")
        db.record_sensor_reading(sensor_id, "co2_ppm",       round(co2,  0), "ppm")
    except Exception:
        pass  # never crash the poll loop


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
        poll_task = asyncio.create_task(_uptime_poll_loop(aggregator, db, registry))

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


@app.get("/api/catalog", tags=["meta"])
async def skill_catalog() -> dict:
    """Return the static skill catalog grouped by platform.

    Each platform contains its instruments; each instrument lists its
    available actions with JSON Schema descriptions of the request body.
    This endpoint is read-only and does not contact any device.
    """
    registry: "Registry" = app.state.registry  # type: ignore[name-defined]

    PLATFORM_LABELS = {"hte": "HTE Platform"}

    # Build a map of kind → [action defs] from the catalog
    def _serialize_actions(kind: str) -> list[dict]:
        defs = SKILL_REGISTRY.get(kind, [])
        result = []
        for d in defs:
            try:
                schema = d.args_schema.model_json_schema()
            except Exception:
                schema = {}
            result.append({
                "name": d.name,
                "description": d.description,
                "method": d.method,
                "endpoint": d.endpoint,
                "args_schema": schema,
                "requires_states": d.requires_states,
                "estimated_duration_s": d.estimated_duration_s,
            })
        return result

    # Group registry entries by platform (exclude env sensors / cameras)
    platforms: dict[str, dict] = {}
    for entry in registry.equipment:
        if entry.kind in ("environmental_sensor", "camera"):
            continue
        p = entry.platform
        if p not in platforms:
            platforms[p] = {
                "label": PLATFORM_LABELS.get(p, p.upper()),
                "instruments": [],
            }
        platforms[p]["instruments"].append({
            "id": entry.id,
            "name": entry.name,
            "kind": entry.kind,
            "adapter": entry.adapter,
            "base_url": entry.base_url or "",
            "protocol": entry.protocol,
            "actions": _serialize_actions(entry.kind),
        })

    return {"platforms": platforms}


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
