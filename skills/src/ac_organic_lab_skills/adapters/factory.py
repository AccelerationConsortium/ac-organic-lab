"""Build the right adapter for an equipment registry entry.

The legacy translators are wired by `equipment.id` (not adapter type) because
each one is bespoke to one device's pre-migration shape. After a device
migrates, change its `adapter` to `http` in `equipment.yaml` and that's it.
"""

from __future__ import annotations

from ..registry import EquipmentEntry
from .base import EquipmentAdapter
from .http_status import HttpStatusAdapter
from .legacy import (
    LegacyDoseEveryWellAdapter,
    LegacyFilterEveryWellAdapter,
    LegacyFumeHoodActuatorAdapter,
    LegacyXArmAdapter,
)
from .mock import MockAdapter


_LEGACY_BY_ID: dict[str, type[EquipmentAdapter]] = {
    "dose_every_well": LegacyDoseEveryWellAdapter,
    "filter_every_well": LegacyFilterEveryWellAdapter,
    "fume_hood_actuator": LegacyFumeHoodActuatorAdapter,
    "xarm_translocation": LegacyXArmAdapter,
}


def build_adapter(entry: EquipmentEntry) -> EquipmentAdapter:
    if entry.adapter == "mock":
        return MockAdapter(entry)
    if entry.adapter == "http":
        return HttpStatusAdapter(entry)
    if entry.adapter == "legacy_http":
        cls = _LEGACY_BY_ID.get(entry.id)
        if cls is None:
            # No bespoke translator for this id; treat the legacy endpoint as if it were
            # already spec-compliant. Worst case the parse_error surfaces in the UI.
            return HttpStatusAdapter(entry)
        return cls(entry)
    raise ValueError(f"Unknown adapter type: {entry.adapter}")
