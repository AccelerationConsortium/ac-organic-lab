"""Equipment adapters.

Adapters take a registry entry and return a unified `EquipmentStatus` envelope
plus aggregator metadata. Failures are classified into a small enum and never
leak as exceptions out of the aggregator.
"""

from __future__ import annotations

from .base import AdapterResult, EquipmentAdapter
from .factory import build_adapter
from .http_status import HttpStatusAdapter
from .legacy import (
    LegacyDoseEveryWellAdapter,
    LegacyFilterEveryWellAdapter,
    LegacyFumeHoodActuatorAdapter,
    LegacyXArmAdapter,
)
from .mock import MockAdapter

__all__ = [
    "AdapterResult",
    "EquipmentAdapter",
    "HttpStatusAdapter",
    "LegacyDoseEveryWellAdapter",
    "LegacyFilterEveryWellAdapter",
    "LegacyFumeHoodActuatorAdapter",
    "LegacyXArmAdapter",
    "MockAdapter",
    "build_adapter",
]
