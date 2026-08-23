"""lab-skills: SDK and aggregator for the AC Organic Self-driving Lab.

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
from .claims import ClaimManager
from .skill_catalog import SKILL_REGISTRY, Skill, SkillDef
from .client import EquipmentClient
from .typed_clients import (
    FumeHoodClient,
    PlateReaderClient,
    PlateSealerClient,
    PressClient,
    RobotArmClient,
    SolidDoserClient,
)
from .exceptions import (
    BadRequest,
    ClaimRejected,
    Degraded,
    EquipmentBusy,
    EquipmentInMaintenance,
    CommandOutcomeUnknown,
    EquipmentUnreachable,
    LabError,
    RegistryError,
    RequiresInit,
    WaitTimeout,
)
from .interlocks import (
    InterlockFn,
    Violation,
    clear_interlocks,
    register_interlock,
    registered_interlocks,
)
from .lab import Lab
from .models import (
    SPEC_VERSION,
    Activity,
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
from .locations import (
    LocationEntry,
    LocationsConfig,
    load_locations,
)
from .platforms import (
    PlatformSection,
    PlatformsConfig,
    load_platforms,
)
from .registry import (
    AdapterKind,
    CameraConfig,
    CameraLens,
    EquipmentEntry,
    Maintenance,
    PillConfig,
    PlugConfig,
    PlugOutlet,
    Registry,
    Tile,
    load_registry,
)
from .plan import (
    Plan,
    PlanReport,
    PlanRunReport,
    Step,
    StepReport,
    StepRunReport,
    execute_plan,
    validate_plan,
)
from .session import LabSession
from .waiting import wait_until_state

__version__ = "0.1.0"

__all__ = [
    "Activity",
    "AdapterKind",
    "BadRequest",
    "CameraConfig",
    "CameraLens",
    "ClaimManager",
    "ClaimRejected",
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
    "CommandOutcomeUnknown",
    "EquipmentUnreachable",
    "ErrorInfo",
    "ErrorSeverity",
    "FetchError",
    "FetchErrorKind",
    "FumeHoodClient",
    "HealthResponse",
    "InterlockFn",
    "Lab",
    "LabError",
    "LabSession",
    "Maintenance",
    "MetricValue",
    "PROTOCOL_VERSION",
    "SPEC_VERSION",
    "PillConfig",
    "Plan",
    "PlanReport",
    "PlanRunReport",
    "PlateReaderClient",
    "PlateSealerClient",
    "LocationEntry",
    "LocationsConfig",
    "PlatformSection",
    "PlatformsConfig",
    "PlugConfig",
    "PlugOutlet",
    "PressClient",
    "ProbeResponse",
    "Registry",
    "RegistryError",
    "RequiresInit",
    "RobotArmClient",
    "SKILL_REGISTRY",
    "Skill",
    "SkillDef",
    "SolidDoserClient",
    "Step",
    "StepReport",
    "StepRunReport",
    "Tile",
    "Violation",
    "WaitTimeout",
    "__version__",
    "clear_interlocks",
    "execute_plan",
    "load_locations",
    "load_platforms",
    "load_registry",
    "register_interlock",
    "registered_interlocks",
    "validate_plan",
    "wait_until_state",
]
