"""Contract re-exports + aggregator runtime types.

The STATUS_SPEC device-contract types (envelope, enums, probe/health bodies)
now come from the shared ``sdl-lab-contract`` package — the promotion this
module's docstring promised since v1.0. They are re-exported here so every
existing ``from lab_skills.models import EquipmentStatus`` (and the package
root's ``from lab_skills import ...``) keeps working unchanged.

The aggregator-only types at the bottom (``FetchError`` family,
``EquipmentSnapshot``, ``EquipmentList``) are **not** part of the device
contract — they describe what the in-process aggregator emits to its
consumers (workflow scripts via the SDK, the dashboard's web server, and
eventually the ``serve`` mode HTTP service in v0.5) — and deliberately stay
local to the SDK.

Conformance: ``lab-skills`` SDK reads lab status spec v1.2 via
``sdl-lab-contract`` 1.2.x (v1.0 / v1.1 / v1.2 devices all parse; the v1.1
and v1.2 additions default to their "undetermined" values so an unmigrated
device is never misread).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from sdl_lab_contract import (
    PROTOCOL_VERSION,
    SPEC_VERSION,
    Activity,
    ComponentStatus,
    EquipmentKind,
    EquipmentState,
    EquipmentStatus,
    ErrorInfo,
    ErrorSeverity,
    HealthResponse,
    MetricValue,
    ProbeResponse,
)

__all__ = [
    # Re-exported contract types (sdl-lab-contract)
    "Activity",
    "ComponentStatus",
    "EquipmentKind",
    "EquipmentState",
    "EquipmentStatus",
    "ErrorInfo",
    "ErrorSeverity",
    "HealthResponse",
    "MetricValue",
    "PROTOCOL_VERSION",
    "ProbeResponse",
    "SPEC_VERSION",
    # Aggregator runtime types (SDK-local)
    "EquipmentList",
    "EquipmentSnapshot",
    "FetchError",
    "FetchErrorKind",
]


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
