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
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import httpx
from lab_skills import EquipmentAggregator, PlatformsConfig, load_platforms, load_registry
from lab_skills.skill_catalog import SKILL_REGISTRY
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .assistant import build_assistant_router
from .alert_notifier import AlertNotifier
from .control import build_control_router
from .workflow import build_workflow_router
from .db import LabDatabase, resolve_db_path
from .deck import build_deck_router
from .events import (
    ACTIVITY_TRANSITION,
    CYCLES_TOTAL_METRIC,
    STATE_TRANSITION,
    snapshot_activity,
    snapshot_reachable,
)
from .history import build_history_router
from .labware import build_labware_router
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
    raw = os.environ.get("DASHBOARD_CORS_ORIGINS", "http://100.64.254.6:8000,http://sdl2-server-gaia.tail6a1dd7.ts.net:8000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# ---------------------------------------------------------------------------
# Background uptime poll task
# ---------------------------------------------------------------------------

# Tracks last known reachability + equipment state per device_id across polls.
_last_reachable: dict[str, bool] = {}
_consecutive_failures: dict[str, int] = {}
_last_state: dict[str, str] = {}
# Last recorded activity (idle/running/unknown) per device — the v1.2 series
# recorded in parallel with _last_state so utilization survives a chronic
# health fault (STATUS_SPEC §2.3).
_last_activity: dict[str, str] = {}

# Per-outlet energy accumulator for power strips/smart plugs.
# Maps device_id → {metric_key → last_seen_today_value}.
# Seeded on the first observation; used to compute cumulative kWh across
# midnight resets (energy_kwh_today resets to 0 at midnight on the device).
_plug_energy_accum: dict[str, dict[str, float]] = {}


# The gateway-fronted kind set and the §2.1 reachability rule live in
# `events.py` (`GATEWAY_FRONTED_KINDS` / `snapshot_reachable`) so the uptime
# recorder, the alert notifier, and the presentation layer share one
# definition.


async def _uptime_poll_loop(
    aggregator: EquipmentAggregator,
    db: LabDatabase,
    registry,
    notifier=None,
) -> None:
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

    # Give the aggregator's background poll loop a moment to fill the cache.
    await asyncio.sleep(5)

    while True:
        try:
            # Read the warm cache rather than fanning out again — the background
            # poll loop already refreshes it far more often than this 60 s loop.
            skill_list = await aggregator.get_snapshot()
            for snap in skill_list.equipment:
                device_id = snap.id
                reg_entry = _registry_map.get(device_id)
                prev = _last_reachable.get(device_id)
                current_state = _state_str(snap)
                current_activity, activity_source = snapshot_activity(snap)

                # Reachability per STATUS_SPEC §2.1 — decided once, shared by
                # every branch below and by the alert notifier. Notably this is
                # *not* just `fetch_error is None`: see `snapshot_reachable`.
                reachable = snapshot_reachable(snap, reg_entry)

                # Why the device is unreachable: a transport failure for a
                # directly-polled device, the gateway's own explanation for a
                # gateway-fronted one (where `fetch_error` is None and would
                # otherwise be logged as the literal string "None").
                unreachable_reason = (
                    str(snap.fetch_error)
                    if snap.fetch_error is not None
                    else (_snap_message(snap) or "gateway cannot reach device")
                )

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
                        STATE_TRANSITION,
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
                        STATE_TRANSITION,
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
                        STATE_TRANSITION,
                        from_state=_last_state.get(device_id, "unknown"),
                        to_state="unreachable",
                        message=unreachable_reason,
                    )
                    _last_state[device_id] = "unreachable"
                    logger.warning("Uptime: %s went DOWN — %s", device_id, unreachable_reason)
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
                            STATE_TRANSITION,
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

                # Activity series (STATUS_SPEC v1.2 §2.3) — recorded in
                # parallel with the state series above, NOT folded into it,
                # so utilization survives a chronic health fault (the
                # motivating case: the SC25XR shaker stuck on `degraded` for
                # weeks while its motor keeps cycling). One uniform block
                # handles every reachability branch: an unreachable device's
                # activity is simply `unknown` (see _activity_str).
                # NOTE §2.3.1: this is a 60 s poll-sampled series — a default
                # 30 s shake can start and finish entirely between polls and
                # is then MISSED, not undercounted. Readers must present it
                # as sampled observation, never as usage accounting.
                prev_activity = _last_activity.get(device_id)
                if prev_activity != current_activity:
                    db.record_equipment_event(
                        device_id,
                        ACTIVITY_TRANSITION,
                        from_state=prev_activity,
                        to_state=current_activity,
                        message=(
                            "Initial observation" if prev_activity is None
                            else "Activity changed"
                        ),
                        payload={"source": activity_source},
                    )
                    _last_activity[device_id] = current_activity
                    logger.info(
                        "Activity: %s %s → %s (%s)",
                        device_id, prev_activity, current_activity, activity_source,
                    )

                # Reserved cycle counter (STATUS_SPEC §2.3.1) — the exact
                # complement to the sampled activity series above: cycles
                # shorter than this loop's 60 s interval are missed by the
                # series but revealed by this counter's poll-to-poll delta.
                _record_cycles_total(db, snap)

                # Device-alert notifier (best-effort; suppressed for
                # maintenance/disabled/mock entries). The notifier owns the
                # debounce/cooldown/storm logic — see alert_notifier.py.
                # Shares the single `reachable` decided at the top of the loop:
                # this block used to apply the §2.1 gateway rule on its own,
                # which is how the alerting and uptime views came to disagree.
                if (
                    notifier is not None
                    and notifier.enabled
                    and reg_entry is not None
                    and reg_entry.enabled
                    and reg_entry.maintenance is None
                    and reg_entry.adapter != "mock"
                ):
                    notifier.observe(
                        device_id,
                        reachable=reachable,
                        state=current_state,
                        message=_snap_message(snap),
                        last_error=_snap_last_error(snap),
                    )

                if (
                    reg_entry is not None
                    and reg_entry.kind in ("smart_plug", "power_strip")
                ):
                    _write_plug_readings(db, snap)
                else:
                    _write_sensor_readings(db, snap)

            if notifier is not None:
                await notifier.flush()

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


def _snap_message(snap) -> Optional[str]:
    """Best human-readable line for an alert: fetch_error, else status.message."""
    try:
        if snap.fetch_error is not None:
            return str(snap.fetch_error)
        return snap.status.message
    except Exception:
        return None


def _snap_last_error(snap) -> Optional[dict]:
    try:
        last_error = snap.status.last_error
        if last_error is None:
            return None
        return last_error.model_dump(mode="json") if hasattr(last_error, "model_dump") else dict(last_error)
    except Exception:
        return None


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


def _record_cycles_total(db: LabDatabase, snap) -> None:
    """Store the raw ``metrics["cycles_total"]`` counter into sensor_readings.

    The raw counter (not the delta) is stored once per sweep, mirroring the
    plug energy counters; ``LabDatabase.get_cycle_count`` computes the
    windowed delta with restart detection at query time. No-op for devices
    that don't publish the reserved key, and automatically for unreachable
    devices (their synthetic envelope carries no metrics).
    """
    try:
        entry = (snap.status.metrics or {}).get(CYCLES_TOTAL_METRIC)
        if entry is None:
            return
        raw = entry.value if hasattr(entry, "value") else entry
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return
        db.record_sensor_reading(snap.id, CYCLES_TOTAL_METRIC, float(raw), "count")
    except Exception:
        pass  # best-effort; never crash the poll loop


#: Environmental metrics persisted to `sensor_readings`, in `status.metrics`
#: key order. These names are the `sense-every-zone` contract (its
#: `_METRIC_MAP`), which the mock adapter mirrors, so mock and real zones take
#: the identical path through this writer.
#:
#: Keys carry no unit suffix — the unit is read off `MetricValue.unit`, per
#: STATUS_SPEC best practice #5. This is a curated subset, not everything the
#: device offers: `pm1` / `pm4` are near-duplicates of `pm25` / `pm10` on a
#: SEN55, and `battery_voltage` is diagnostic noise at one row/minute. Both
#: remain visible on the live tile via `status.metrics`; they are just not
#: worth 1,440 rows/day each.
_ENV_HISTORY_METRICS = (
    "temperature", "humidity", "voc", "nox", "pm25", "pm10",
    "co", "o2", "h2", "battery",
)


def _write_sensor_readings(db: LabDatabase, snap) -> None:
    """Write environmental metrics from a device snapshot (no-op if none).

    Reads `status.metrics` — where STATUS_SPEC puts measurements — for both
    real and mock zones. Values that are non-numeric (or absent, e.g. a basic
    zone node has no `o2` cell) are skipped rather than coerced, so a missing
    channel never lands as a phantom zero.
    """
    try:
        metrics = getattr(snap.status, "metrics", None) or {}
        for name in _ENV_HISTORY_METRICS:
            entry = metrics.get(name)
            if entry is None:
                continue
            raw = entry.value if hasattr(entry, "value") else entry
            if raw is None or isinstance(raw, bool):
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue  # a device reporting a string metric is not history
            unit = getattr(entry, "unit", None)
            db.record_sensor_reading(snap.id, name, value, unit)
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


# Synthetic readings are no longer generated here. A mock zone's fake values
# come from the SDK's `MockAdapter`, which mirrors a real `sense-every-zone`
# envelope, so `_write_sensor_readings` above persists mock and real zones
# through one code path. (Removed 2026-07-31 along with the suffixed
# `temperature_c` / `humidity_pct` / `co2_ppm` names it wrote; `co2` was always
# fictional — no sensor in the lab measures it.)


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Equipment registry + aggregator
    registry = load_registry()
    aggregator = EquipmentAggregator(
        registry,
        poll_interval_s=float(os.environ.get("AGGREGATOR_POLL_INTERVAL_S", "2.5")),
    )
    await aggregator.startup()
    # Start the background poll loop so /api/equipment serves a warm cache
    # (memory read) instead of fanning out to every device per request. One
    # loop feeds all viewers; a slow/dead device never stalls the dashboard.
    await aggregator.start_polling()
    app.state.aggregator = aggregator
    app.state.registry = registry
    # Long-lived httpx client for the control passthrough. Sharing one
    # client across requests is what unlocks HTTP/1.1 keep-alive: the
    # first POST to a device pays the TCP handshake; subsequent POSTs
    # reuse the warm socket. Per STATUS_SPEC v1.1 every control click
    # does 3 sequential round-trips (claim → action → release), so a
    # cold connection used to cost ~3 × handshake. trust_env=False
    # matches the aggregator's stance (no proxy auto-discovery).
    app.state.control_client = httpx.AsyncClient(
        trust_env=False,
        timeout=httpx.Timeout(15.0),
    )
    app.state.overrides = load_dashboard_overrides()
    app.state.platforms_config = load_platforms()
    logger.info("Loaded equipment registry: %d entries", aggregator.equipment_count)
    logger.info(
        "Loaded platforms config: %d sections",
        len(app.state.platforms_config.sections),
    )

    # Lab history database
    db_path = resolve_db_path()
    db = LabDatabase(db_path)
    try:
        db.open()
    except Exception as exc:
        logger.error("Could not open lab database at %s: %s — history endpoints disabled", db_path, exc)
        db = None  # type: ignore[assignment]
    app.state.db = db

    # Background uptime poll (+ device-alert notifier, enabled only when
    # PYPOE_ALERT_URL is set — see alert_notifier.py).
    poll_task = None
    if db is not None:
        notifier = AlertNotifier(db=db, client=app.state.control_client)
        if notifier.enabled:
            logger.info("Device-alert notifier enabled → %s", notifier.url)
        poll_task = asyncio.create_task(
            _uptime_poll_loop(aggregator, db, registry, notifier=notifier)
        )

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
        await app.state.control_client.aclose()
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

# Phase F: authorized plan execution (D-20 — the runner lives here, not in
# bitácora, because this app already owns the claim and the audit row).
app.include_router(build_workflow_router())

app.include_router(build_deck_router())
# Central custom-labware definition store (repo-committed + admin uploads).
app.include_router(build_labware_router())
# History + ingest endpoints (SQLite-backed).
app.include_router(build_history_router())
# Read-only Claude assistant -- streams chat over SSE, has tool access to
# the history DB and a whitelisted set of systemd journals. See assistant.py.
app.include_router(build_assistant_router())


def _aggregator() -> EquipmentAggregator:
    aggregator: EquipmentAggregator | None = getattr(app.state, "aggregator", None)
    if aggregator is None:
        raise HTTPException(status_code=503, detail="Aggregator not initialized")
    return aggregator


@app.get("/api/platforms", response_model=PlatformsConfig, tags=["meta"])
async def list_platforms() -> PlatformsConfig:
    """Return the static platforms configuration."""
    return app.state.platforms_config


@app.get("/api/health", response_model=AggregatorHealth, tags=["meta"])
async def health() -> AggregatorHealth:
    aggregator = _aggregator()
    return AggregatorHealth(
        version=__version__,
        equipment_count=aggregator.equipment_count,
    )


@app.get("/status", tags=["meta"])
async def equipment_status() -> dict:
    """STATUS_SPEC v1.0 envelope for the dashboard API service *itself*, so it
    can appear as a tile under the dashboard's own "Web Services" section.

    Side-effect-free (best-practice #1): only reads already-warm in-process
    state. The aggregator polls this on loopback like any other device.
    """
    aggregator: EquipmentAggregator | None = getattr(app.state, "aggregator", None)
    return {
        "protocol_version": "1.0",
        "equipment_id": "ac_organic_lab_api",
        "equipment_name": "Dashboard API",
        "equipment_kind": "other",
        "equipment_version": __version__,
        "host": socket.gethostname(),
        "equipment_status": "ready" if aggregator is not None else "requires_init",
        "device_time": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            "equipment_count": {
                "value": aggregator.equipment_count if aggregator is not None else 0,
                "unit": "devices",
            },
        },
        "details": {},
    }


@app.get("/api/equipment", response_model=EquipmentList, tags=["equipment"])
async def list_equipment() -> EquipmentList:
    """Return the latest status of every registered equipment in parallel."""

    aggregator = _aggregator()
    skill_list = await aggregator.get_snapshot()
    return compose_equipment_list(
        skill_list,
        app.state.overrides,
        app.state.registry,
        app.state.platforms_config,
    )


@app.get("/api/catalog", tags=["meta"])
async def skill_catalog() -> dict:
    """Return the static skill catalog grouped by platform.

    Each platform contains its instruments; each instrument lists its
    available actions with JSON Schema descriptions of the request body.
    This endpoint is read-only and does not contact any device.
    """
    registry: "Registry" = app.state.registry  # type: ignore[name-defined]
    platforms_config: "PlatformsConfig" = app.state.platforms_config  # type: ignore[name-defined]

    eq_to_section = platforms_config.equipment_to_section_id()
    section_titles = {s.id: s.title for s in platforms_config.sections}

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

    # Group registry entries by section (exclude env sensors / cameras)
    platforms: dict[str, dict] = {}
    for entry in registry.equipment:
        if entry.kind in ("environmental_sensor", "camera"):
            continue
        section_id = eq_to_section.get(entry.id, "unknown")
        if section_id not in platforms:
            platforms[section_id] = {
                "label": section_titles.get(section_id, section_id.upper()),
                "instruments": [],
            }
        platforms[section_id]["instruments"].append({
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
    return _snapshot(sdk_snapshot, override, app.state.registry, app.state.platforms_config)
