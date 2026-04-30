"""Pydantic models implementing the unified equipment status spec.

The authoritative copy of these definitions lives in `docs/STATUS_SPEC.md` at the
repository root. Equipment repos copy this file into their own packages until the
spec is promoted into a `lab-status-contract` Python package.
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
    "ready",
    "busy",
    "requires_init",
    "degraded",
    "dry_run",
    "error",
    "e_stop",
    "unknown",
]

ErrorSeverity = Literal["info", "warning", "error", "critical"]


class ComponentStatus(BaseModel):
    connected: bool
    state: str
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

    equipment_id: str
    equipment_name: str
    equipment_kind: EquipmentKind
    equipment_version: str | None = None
    host: str | None = None

    equipment_status: EquipmentState
    message: str | None = None
    required_actions: list[str] = Field(default_factory=list)

    device_time: datetime
    uptime_seconds: float | None = None

    components: dict[str, ComponentStatus] = Field(default_factory=dict)
    metrics: dict[str, MetricValue] = Field(default_factory=dict)
    last_error: ErrorInfo | None = None

    details: dict[str, Any] = Field(default_factory=dict)


class ProbeResponse(BaseModel):
    equipment_id: str
    equipment_name: str
    protocol_version: str = PROTOCOL_VERSION


class HealthResponse(BaseModel):
    status: Literal["healthy"] = "healthy"


# -- Aggregator-only types ---------------------------------------------------


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


class Location(BaseModel):
    """Position of an equipment on the lab floorplan map (percentages 0-100)."""

    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    label: str | None = None


class Tile(BaseModel):
    """Tile size for the dashboard equipment grid.

    The platform card lays equipment out on a 4-column CSS grid with fixed-height
    rows. `w` is the number of columns the tile spans (1..4) and `h` is the
    number of rows it spans (1..4). Default 2x1 = current half-row layout.
    """

    w: int = Field(default=2, ge=1, le=4)
    h: int = Field(default=1, ge=1, le=4)


class EquipmentSnapshot(BaseModel):
    """What the dashboard frontend consumes for one equipment.

    Wraps `EquipmentStatus` with aggregator-side metadata (when we last fetched
    it, how long it took, and whether the latest fetch errored). The `status`
    field is always present - on first failure the aggregator emits a synthetic
    `unknown` envelope so the UI never has to deal with `null`.
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
    location: Location | None = None
    tile: Tile = Field(default_factory=Tile)


class EquipmentList(BaseModel):
    equipment: list[EquipmentSnapshot]
    fetched_at: datetime


class AggregatorHealth(BaseModel):
    status: Literal["healthy"] = "healthy"
    version: str
    equipment_count: int
