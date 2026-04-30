"""Equipment registry loader.

The committed `equipment.yaml` at the repo root is the single source of truth
for "where to reach each device". Tailscale hostnames are not treated as
secrets in this project.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from .models import EquipmentKind, Tile


AdapterKind = Literal["http", "legacy_http", "mock"]


class Location(BaseModel):
    """Position of an equipment on the lab floorplan map.

    Coordinates are percentages (0-100) of the map's width and height so the
    layout is independent of the SVG's pixel size. `label` is the human-readable
    spot name (e.g. "North Bench").
    """

    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    label: str | None = None


class EquipmentEntry(BaseModel):
    id: str
    name: str
    platform: str
    kind: EquipmentKind
    adapter: AdapterKind
    base_url: str | None = None
    status_path: str = "/status"
    poll_timeout_seconds: float = 2.0
    do_not_call_connect: bool = False
    location: Location | None = None
    tile: Tile = Field(default_factory=Tile)
    extras: dict = Field(default_factory=dict)


class Registry(BaseModel):
    equipment: list[EquipmentEntry]

    def by_id(self, equipment_id: str) -> EquipmentEntry | None:
        for e in self.equipment:
            if e.id == equipment_id:
                return e
        return None


def _default_registry_path() -> Path:
    """Find `equipment.yaml` at the repo root (one level above `api/`)."""

    return Path(__file__).resolve().parents[2] / "equipment.yaml"


def load_registry(path: str | os.PathLike | None = None) -> Registry:
    """Load the equipment registry from YAML.

    Path resolution order:
      1. Argument `path`, if provided.
      2. `LAB_REGISTRY_PATH` environment variable.
      3. `<repo-root>/equipment.yaml`.
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
