"""ac-organic-lab-skills: SDK and aggregator for the AC Organic Self-driving Lab.

This package owns:

* the equipment registry (parsed from the repo-root ``equipment.yaml``),
* the async polling aggregator and per-device adapters,
* the workflow-facing session API (``Lab.connect()`` / ``LabSession`` /
  ``EquipmentClient``).

The dashboard server (``api/``) and project workflow repos both import from
this package; per ``docs/ARCHITECTURE.md`` it is the single authoritative
layer for control and runtime equipment state.
"""

from __future__ import annotations

from .aggregator import EquipmentAggregator
from .client import EquipmentClient
from .exceptions import (
    BadRequest,
    Degraded,
    EquipmentBusy,
    EquipmentInMaintenance,
    EquipmentUnreachable,
    LabError,
    RegistryError,
    RequiresInit,
    WaitTimeout,
)
from .lab import Lab
from .models import (
    ComponentStatus,
    EquipmentKind,
    EquipmentList,
    EquipmentSnapshot,
    EquipmentState,
    EquipmentStatus,
    ErrorInfo,
    ErrorSeverity,
    FetchError,
    FetchErrorKind,
    HealthResponse,
    MetricValue,
    PROTOCOL_VERSION,
    ProbeResponse,
)
from .registry import (
    AdapterKind,
    EquipmentEntry,
    Maintenance,
    Registry,
    load_registry,
)
from .session import LabSession
from .waiting import wait_until_state

__version__ = "0.1.0"

__all__ = [
    "AdapterKind",
    "BadRequest",
    "ComponentStatus",
    "Degraded",
    "EquipmentAggregator",
    "EquipmentBusy",
    "EquipmentClient",
    "EquipmentEntry",
    "EquipmentInMaintenance",
    "EquipmentKind",
    "EquipmentList",
    "EquipmentSnapshot",
    "EquipmentState",
    "EquipmentStatus",
    "EquipmentUnreachable",
    "ErrorInfo",
    "ErrorSeverity",
    "FetchError",
    "FetchErrorKind",
    "HealthResponse",
    "Lab",
    "LabError",
    "LabSession",
    "Maintenance",
    "MetricValue",
    "PROTOCOL_VERSION",
    "ProbeResponse",
    "Registry",
    "RegistryError",
    "RequiresInit",
    "WaitTimeout",
    "__version__",
    "load_registry",
    "wait_until_state",
]
