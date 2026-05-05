"""Equipment registry loader.

The committed ``equipment.yaml`` at the monorepo root is the single source of
truth for "what equipment exists in this lab and where to reach it". This
module parses the SDK-relevant fields. Dashboard-only presentation fields
(``tile``, ``location``) are parsed separately by ``api/app/presentation.py``
from the same file.

Tailscale hostnames are not treated as secrets in this project.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from .models import EquipmentKind


AdapterKind = Literal["http", "legacy_http", "mock"]


class Maintenance(BaseModel):
    """Soft-maintenance metadata for an equipment registry entry.

    Set on an entry to mark a device as offline for maintenance without
    removing it from ``equipment.yaml``. The SDK raises
    ``EquipmentInMaintenance`` from ``Lab.get(<id>)`` when an entry is in
    maintenance; the dashboard surfaces the same metadata on its tile.
    """

    reason: str
    until: date | None = None
    contact: str | None = None


class EquipmentEntry(BaseModel):
    """One device's entry in ``equipment.yaml`` (SDK view).

    Carries every field the SDK and aggregator need. Dashboard-only fields
    (``tile``, ``location``) are NOT on this model - they are parsed
    separately by ``api/app/presentation.py``.
    """

    id: str
    name: str
    platform: str
    kind: EquipmentKind
    adapter: AdapterKind
    base_url: str | None = None
    status_path: str = "/status"
    poll_timeout_seconds: float = 2.0
    do_not_call_connect: bool = False

    # Soft maintenance toggling. ``enabled: false`` (or a non-null
    # ``maintenance``) makes ``Lab.get(<id>)`` raise
    # ``EquipmentInMaintenance``. The aggregator still polls the device so
    # the dashboard can show its tile in maintenance state.
    enabled: bool = True
    maintenance: Maintenance | None = None

    extras: dict = Field(default_factory=dict)


class Registry(BaseModel):
    equipment: list[EquipmentEntry]

    def by_id(self, equipment_id: str) -> EquipmentEntry | None:
        for e in self.equipment:
            if e.id == equipment_id:
                return e
        return None


def _default_registry_path() -> Path:
    """Walk parent directories from this file looking for ``equipment.yaml``.

    Works whether the SDK is imported from inside the monorepo (one shared
    ``equipment.yaml`` at the repo root) or installed elsewhere. Callers that
    need a different file should pass ``path=`` to ``load_registry`` or set
    ``LAB_REGISTRY_PATH``.
    """

    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "equipment.yaml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not locate equipment.yaml in any ancestor directory of "
        f"{here}; pass an explicit path to load_registry() or set "
        "LAB_REGISTRY_PATH."
    )


def load_registry(path: str | os.PathLike | None = None) -> Registry:
    """Load the equipment registry from YAML.

    Path resolution order:

    1. Argument ``path``, if provided.
    2. ``LAB_REGISTRY_PATH`` environment variable.
    3. The first ``equipment.yaml`` found by walking up from this module.
    """

    resolved: Path
    if path is not None:
        resolved = Path(path)
    elif os.environ.get("LAB_REGISTRY_PATH"):
        resolved = Path(os.environ["LAB_REGISTRY_PATH"])
    else:
        resolved = _default_registry_path()

    if not resolved.exists():
        raise FileNotFoundError(f"Equipment registry not found at {resolved}")

    with resolved.open("r") as f:
        data = yaml.safe_load(f) or {}

    try:
        return Registry.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid equipment registry at {resolved}: {exc}") from exc
