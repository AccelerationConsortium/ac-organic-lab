"""Platform configuration loader.

The committed ``platforms.yaml`` at the monorepo root defines which sections
appear on the Overview page, in what order, and which equipment ids belong to
each section.  This module parses it into a typed ``PlatformsConfig``.

Missing file or invalid schema raises immediately (no fallback to defaults).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError


class PlatformSection(BaseModel):
    """One section in ``platforms.yaml``."""

    id: str
    title: str
    description: str | None = None
    href: str | None = None
    kind: Literal["platform", "environmental_map"]
    equipment: list[str]


class PlatformsConfig(BaseModel):
    """Parsed ``platforms.yaml``."""

    sections: list[PlatformSection]

    def equipment_to_section_id(self) -> dict[str, str]:
        """Return a mapping from equipment id to section id.

        First section that lists an id wins (sections are in display order).
        """
        result: dict[str, str] = {}
        for section in self.sections:
            for eq_id in section.equipment:
                if eq_id not in result:
                    result[eq_id] = section.id
        return result

    def section_for_equipment(self, equipment_id: str) -> PlatformSection | None:
        section_id = self.equipment_to_section_id().get(equipment_id)
        if section_id is None:
            return None
        return next((s for s in self.sections if s.id == section_id), None)


def _default_platforms_path() -> Path:
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "platforms.yaml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not locate platforms.yaml in any ancestor directory of "
        f"{here}; pass an explicit path to load_platforms() or set "
        "LAB_PLATFORMS_PATH."
    )


def load_platforms(path: str | os.PathLike | None = None) -> PlatformsConfig:
    """Load the platforms configuration from YAML.

    Path resolution order:

    1. Argument ``path``, if provided.
    2. ``LAB_PLATFORMS_PATH`` environment variable.
    3. The first ``platforms.yaml`` found by walking up from this module.
    """

    resolved: Path
    if path is not None:
        resolved = Path(path)
    elif os.environ.get("LAB_PLATFORMS_PATH"):
        resolved = Path(os.environ["LAB_PLATFORMS_PATH"])
    else:
        resolved = _default_platforms_path()

    if not resolved.exists():
        raise FileNotFoundError(f"Platforms config not found at {resolved}")

    with resolved.open("r") as f:
        data = yaml.safe_load(f) or {}

    try:
        return PlatformsConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid platforms config at {resolved}: {exc}"
        ) from exc
