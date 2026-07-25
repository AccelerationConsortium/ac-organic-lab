"""Lab equipment status spec v1.2 + aggregator runtime types.

The STATUS_SPEC portion of this file (everything from ``SPEC_VERSION`` down
through ``HealthResponse``) is the authoritative copy that mirrors
``docs/STATUS_SPEC.md`` at the repo root and is kept verbatim-identical to the
copies vendored into per-device repos (each carries its own ``models.py``).
Once a shared ``lab-status-contract`` package ships, this section will be
replaced by ``from lab_status_contract import ...``.

The aggregator-only types at the bottom (``FetchError`` family,
``EquipmentSnapshot``, ``EquipmentList``) are not part of the device contract
- they describe what the in-process aggregator emits to its consumers
(workflow scripts via the SDK, the dashboard's web server, and eventually the
``serve`` mode HTTP service in v0.5).

Conformance: ``lab-skills`` SDK reads lab status spec v1.2 (v1.0 / v1.1 / v1.2
devices all parse; the v1.1 and v1.2 additions default to their
"undetermined" values so an unmigrated device is never misread).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# The spec revision the models in this module mirror. Bump this in lockstep
# with ``docs/STATUS_SPEC.md``.
SPEC_VERSION = "1.2"

# Default for the ``protocol_version`` *field* when a device omits it. This is
# deliberately NOT ``SPEC_VERSION``: a device that does not state its version
# is a pre-spec / v1.0 device, and reading it as v1.2 would claim guarantees
# (claims, ``allowed_actions``, ``activity``) it never made. Device repos
# vendoring this file override the constant with the version they actually
# speak.
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
    "shaker",        # orbital shakers with integrated heater (e.g. Torrey Pines SC20)
    # Lab-LAN devices fronted by ``kasa-tapo-services``. Cameras carry a
    # ``details.lenses[]`` + ``details.presets[]`` block; plugs carry one
    # ``ComponentStatus`` per outlet (``outlet_0`` … ``outlet_5`` for the
    # HS300, ``plug`` for HS103/HS105/HS110 single-outlet devices).
    "camera",
    "smart_plug",
    "power_strip",
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

# NEW in v1.2 (STATUS_SPEC §2.3). Orthogonal to ``EquipmentState``: health and
# activity are independent questions, and ``equipment_status`` answers only the
# first (§2.2 requires a fault to claim the top-level state). A device MUST
# derive this from observed hardware, never from ``equipment_status``.
Activity = Literal[
    "idle",           # not performing its primary operation
    "running",        # primary operation in progress
    "unknown",        # cannot be determined - answer of last resort
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
    """Unified equipment status envelope (spec v1.0 + v1.1 + v1.2 additions).

    All three versions parse through this one model; the later additions carry
    defaults chosen so an unmigrated device is read as "did not say", never as
    a positive claim it never made.

    v1.1: :attr:`allowed_actions` is the device's own declaration of what it
    would honor right now. v1.0 devices that omit it see an empty list, and
    the catalog falls back to ``equipment_status in def.requires_states``.
    ``details.claimed_by`` (a ``ClaimedBy``-shaped dict, or absent) stays
    nested under :attr:`details` to keep the top-level shape stable for v1.0
    readers; :mod:`lab_skills.claims` parses it.

    v1.2: :attr:`activity` / :attr:`activity_since` answer "is it working",
    which :attr:`equipment_status` cannot express once §2.2 gives the
    top-level state to health. A v1.0/v1.1 device omitting them reads as
    ``"unknown"`` - never as a false ``"idle"``.
    """

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
    allowed_actions: list[str] = Field(default_factory=list)

    # NEW in v1.2 (§2.3) - orthogonal to `equipment_status`. The defaults are
    # deliberate: an older device that omits these reads as "undetermined",
    # never as a false "idle".
    activity: Activity = "unknown"
    # Start of the current activity span (the instant `activity` last changed
    # value), not the start of the enclosing request or process.
    activity_since: datetime | None = None

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
    platform: str | None = None
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
