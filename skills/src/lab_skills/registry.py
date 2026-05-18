"""Equipment registry loader.

The committed ``equipment.yaml`` at the monorepo root is the single source of
truth for "what equipment exists in this lab and where to reach it". This
module parses the SDK-relevant fields. Dashboard-only presentation fields
(``location``) are parsed separately by ``api/app/presentation.py``
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


class Tile(BaseModel):
    """Tile size for the dashboard equipment grid.

    The platform card lays equipment out on a 4-column CSS grid with
    fixed-height rows.  ``w`` is the number of columns (1..4) and ``h``
    is the number of rows (1..4).  Default 2×1.
    """

    w: int = Field(default=2, ge=1, le=4)
    h: int = Field(default=1, ge=1, le=4)


class PillConfig(BaseModel):
    """Pill configuration for the Overview platform-card row.

    ``open: true`` renders an "Open ↗" link to the equipment's ``base_url``.
    Extensible: add ``label``, ``icon``, etc. without breaking older entries.
    """

    open: bool = False


# Protocol version a device claims to implement. Drives client-side behavior
# for the claim/heartbeat/release protocol introduced in the v1.1 section of
# ``docs/STATUS_SPEC.md``. Devices stay on ``"1.0"`` (the default) until their
# repo has been migrated and the yaml entry flips this field. The SDK never
# auto-detects from a live ``/status`` to keep ``validate_plan`` offline.
DeviceProtocol = Literal["1.0", "1.1"]


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


class CameraLens(BaseModel):
    """One physical lens on a multi-lens camera (``kind: camera``)."""

    id: str
    label: str
    rtsp_path: str = "stream1"
    ptz_capable: bool = True
    """False for fixed lenses with no PTZ motor (e.g. wide on Tapo C245D).
    The dashboard uses this to grey out the PTZ pad when the user selects
    a non-moveable lens."""


class CameraConfig(BaseModel):
    """Optional ``camera:`` block on entries with ``kind: camera``.

    Mirrors the gateway's ``devices.yaml``. The dashboard reads it for the
    lens-tab labels in the camera tile and the go2rtc stream-name
    convention (``<equipment_id>_<lens_id>``).
    """

    host: str
    onvif_port: int = 2020
    rtsp_port: int = 554
    lenses: list[CameraLens] = Field(default_factory=list)


class PlugOutlet(BaseModel):
    """One labelled outlet on a multi-outlet plug (``kind: power_strip``)."""

    index: int = Field(ge=0, le=31)
    label: str | None = None


class PlugConfig(BaseModel):
    """Optional ``plug:`` block on ``smart_plug`` and ``power_strip`` entries."""

    host: str
    outlets: list[PlugOutlet] = Field(default_factory=list)


class EquipmentEntry(BaseModel):
    """One device's entry in ``equipment.yaml`` (SDK view).

    Carries every field the SDK and aggregator need. Dashboard-only fields
    (``location``) are NOT on this model - they are parsed separately by
    ``api/app/presentation.py``.

    ``tiles`` is keyed by platform section id (from ``platforms.yaml``).
    ``pills`` is the shared Overview pill config for this equipment.
    """

    id: str
    name: str
    kind: EquipmentKind
    adapter: AdapterKind
    base_url: str | None = None
    status_path: str = "/status"
    poll_timeout_seconds: float = 2.0
    do_not_call_connect: bool = False

    tiles: dict[str, Tile] = Field(default_factory=dict)
    pills: PillConfig = Field(default_factory=PillConfig)

    # STATUS_SPEC version the device implements. ``"1.1"`` means the device
    # exposes ``POST /control/{claim,heartbeat,release}`` and populates
    # ``allowed_actions`` on ``/status``. ``"1.0"`` (the default) means the
    # device follows the v1.0 contract and the SDK degrades claim semantics
    # to a no-op for it.
    protocol: DeviceProtocol = "1.0"

    # Soft maintenance toggling. ``enabled: false`` (or a non-null
    # ``maintenance``) makes ``Lab.get(<id>)`` raise
    # ``EquipmentInMaintenance``. The aggregator still polls the device so
    # the dashboard can show its tile in maintenance state.
    enabled: bool = True
    maintenance: Maintenance | None = None

    # Optional kind-specific blocks. Cameras emit lens info; multi-outlet
    # plugs emit per-outlet labels. Both blocks are dashboard-only -
    # neither the SDK's session API nor the aggregator looks at them; the
    # camera tile in ``web/`` reads them through ``EquipmentSnapshot``.
    camera: CameraConfig | None = None
    plug: PlugConfig | None = None

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
