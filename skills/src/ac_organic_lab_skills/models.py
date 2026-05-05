"""Lab equipment status spec v1.0 + aggregator runtime types.

The STATUS_SPEC v1.0 portion of this file (everything from ``PROTOCOL_VERSION``
down through ``HealthResponse``) is the authoritative copy that mirrors
``docs/STATUS_SPEC.md`` at the repo root and is kept verbatim-identical to the
copies vendored into per-device repos (see ``agilent_plateloc/src/agilent_plateloc/models.py``).
Once a shared ``lab-status-contract`` package ships, this section will be
replaced by ``from lab_status_contract import ...``.

The aggregator-only types at the bottom (``FetchError`` family,
``EquipmentSnapshot``, ``EquipmentList``) are not part of the device contract
- they describe what the in-process aggregator emits to its consumers
(workflow scripts via the SDK, the dashboard's web server, and eventually the
``serve`` mode HTTP service in v0.5).

Conformance: ``ac-organic-lab-skills`` SDK conforms to lab status spec v1.0.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

PROTOCOL_VERSION = "1.0"


EquipmentKind = Literal[
    "solid_doser",
    "liquid_handler",
    "press",
    "fume_hood",
    "robot_arm",
    "environmental_sensor",
    "hplc",
    "plate_reader",
    "plate_sealer",
    "plate_stacker",
    "other",
]

EquipmentState = Literal[
    "ready",          # initialized, idle, can accept commands
    "busy",           # performing an operation
    "requires_init",  # service up but hardware not initialized (e.g. needs POST /control/startup)
    "degraded",       # running but a sub-component is unhealthy
    "dry_run",        # simulation mode, no hardware connected
    "error",          # hardware reported an error
    "e_stop",         # emergency stopped
    "unknown",        # state cannot be determined
]

ErrorSeverity = Literal["info", "warning", "error", "critical"]


class ComponentStatus(BaseModel):
    connected: bool
    state: str  # equipment-defined string; pick a small enum per equipment kind
    message: str | None = None
    last_event_at: datetime | None = None


class MetricValue(BaseModel):
    value: float | int | str | bool
    unit: str | None = None
    timestamp: datetime | None = None


class ErrorInfo(BaseModel):
    code: str | None = None
    message: str
    severity: ErrorSeverity
    timestamp: datetime


class EquipmentStatus(BaseModel):
    """Unified equipment status envelope (spec v1.0)."""

    protocol_version: str = PROTOCOL_VERSION

    # Identity
    equipment_id: str
    equipment_name: str
    equipment_kind: EquipmentKind
    equipment_version: str | None = None
    host: str | None = None  # local hostname only (output of `hostname`)

    # Operational state
    equipment_status: EquipmentState
    message: str | None = None
    required_actions: list[str] = Field(default_factory=list)

    # Timing
    device_time: datetime
    uptime_seconds: float | None = None

    # Sub-equipment / measurements
    components: dict[str, ComponentStatus] = Field(default_factory=dict)
    metrics: dict[str, MetricValue] = Field(default_factory=dict)
    last_error: ErrorInfo | None = None

    # Free-form per-equipment data; safe to display in a debug/details panel.
    details: dict[str, Any] = Field(default_factory=dict)


class ProbeResponse(BaseModel):
    """Body of `GET /` - the cheapest possible identity probe."""

    equipment_id: str
    equipment_name: str
    protocol_version: str = PROTOCOL_VERSION


class HealthResponse(BaseModel):
    """Body of `GET /health` - service liveness."""

    status: Literal["healthy"] = "healthy"


# -- Aggregator runtime types (not part of the device contract) --------------


FetchErrorKind = Literal[
    "timeout",
    "connection_refused",
    "http_4xx",
    "http_5xx",
    "parse_error",
    "unconfigured",
    "unknown",
]


class FetchError(BaseModel):
    """Set by the aggregator when an adapter fails to fetch a fresh status."""

    kind: FetchErrorKind
    message: str
    http_status: int | None = None


class EquipmentSnapshot(BaseModel):
    """Per-equipment view emitted by the aggregator.

    Combines a normalised ``EquipmentStatus`` envelope (or a synthetic
    ``unknown``-state envelope on fetch failure) with the registry identity
    fields and aggregator timing/error metadata. This is the SDK's
    public per-device output shape; the dashboard wraps it in
    ``api/app/presentation.py`` to add presentation-only fields like
    ``tile`` and ``location``.
    """

    id: str
    name: str
    platform: str
    kind: EquipmentKind
    adapter: str

    status: EquipmentStatus
    fetched_at: datetime
    latency_ms: int | None = None
    fetch_error: FetchError | None = None
    base_url: str | None = None


class EquipmentList(BaseModel):
    """Batch view: one ``EquipmentSnapshot`` per registered equipment."""

    equipment: list[EquipmentSnapshot]
    fetched_at: datetime
