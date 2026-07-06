"""Platform↔equipment membership for grant resolution (Phase 1).

The auth model **reuses the dashboard's `platforms.yaml`** for membership rather
than duplicating it: a `platform`-scoped grant on platform ``hte`` applies to
every device listed under the ``hte`` section. This module reads that file into a
simple ``equipment_key -> {platform_id, ...}`` map.

Fail-soft on purpose: a missing/unreadable/!malformed `platforms.yaml` yields an
**empty** map, which makes `platform`-scoped grants resolve to nothing — i.e. it
fails toward *less* access (global + equipment grants still work), never more.
Only ``kind: platform`` sections count as platforms (an ``environmental_map`` is
a map widget, not a control platform).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml


def default_platforms_path() -> Path:
    override = os.environ.get("AUTH_PLATFORMS_PATH")
    if override:
        return Path(override)
    # ac_auth/platforms.py -> ac_auth -> auth -> repo root -> platforms.yaml
    return Path(__file__).resolve().parents[2] / "platforms.yaml"


def load_membership(path: Path | str | None = None) -> dict[str, set[str]]:
    """Return ``{equipment_key: {platform_id, ...}}`` from `platforms.yaml`.
    Empty dict if the file is absent or unparseable (fail-soft → less access)."""
    p = Path(path) if path else default_platforms_path()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    membership: dict[str, set[str]] = {}
    for section in data.get("sections", []) or []:
        if not isinstance(section, dict) or section.get("kind") != "platform":
            continue
        platform_id = section.get("id")
        if not platform_id:
            continue
        for equipment_key in section.get("equipment", []) or []:
            membership.setdefault(equipment_key, set()).add(platform_id)
    return membership


__all__ = ["load_membership", "default_platforms_path"]
