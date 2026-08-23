"""Location registry loader — the places a plate (or any container) can be.

The committed ``locations.yaml`` at the monorepo root enumerates every
nameable physical place in the lab that custody tracking can refer to: device
positions (a deck slot, a reader carrier, a sealer stage, the arm's gripper),
storage spots, and waste. This module parses it into a typed
``LocationsConfig``.

It is the third of the three root YAML files and answers the third question
(ARCHITECTURE.md decision #5):

- ``equipment.yaml`` — what hardware exists and how to reach it
- ``platforms.yaml`` — how the UI presents it
- ``locations.yaml`` — **where can a thing be**

What this file is **not** (``docs/PLATE_TRACKING.md``):

- It is not state. "Where is plate X *now*" lives in the record layer
  (AnaliticaDB ``Container.location_id`` + the ``ContainerAction`` ledger);
  this registry only names the places and seeds the ``Location`` table. The
  yaml never carries state; the database never invents places.
- It is not a state machine. Which moves are legal is device-authoritative
  (the xArm motion graph's reachable nodes, the OT-2 deck) and is deliberately
  not duplicated here.
- Entries are **custody places, not floor-plan pins**. ``equipment.yaml``
  already has a ``location: {x, y, label}`` key on environmental sensors (the
  lab-map marker, ``api/app/presentation.py``); that key is unrelated and must
  never be reused for these.

Names are identifiers and immutable: renaming a place is a new entry plus
``active: false`` on the old one, because ledger history points at names.
``aliases`` are observation-only vocabulary — they let a reader map a device's
own words (an OT-2 slot key, an xArm graph node) back to the canonical name
when *reading* a device snapshot; they are never used to *infer* a move.

Missing file or invalid schema raises immediately (no fallback to defaults),
matching ``load_registry`` / ``load_platforms``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from .registry import Registry

#: Mirrors AnaliticaDB ``Location.location_type`` (DATABASE_DESIGN.md §6).
#: Deliberately no ``transport``: a plate in transit sits in the arm's gripper,
#: which is itself an ``instrument`` location with ``capacity: 1``.
LocationType = Literal["storage", "instrument", "deck", "fridge", "waste"]

#: Slash-path names: ``<equipment_id>/<position>`` for device-anchored places,
#: ``<site>/<path>`` otherwise. At least one ``/``; lowercase snake segments.
NAME_RE = re.compile(r"^[a-z0-9_]+(/[a-z0-9_]+)+$")


class LocationEntry(BaseModel):
    """One place in ``locations.yaml``."""

    #: Immutable identifier (see module docstring). Slash-path.
    name: str
    #: The AnaliticaDB ``location_type`` this seeds.
    type: LocationType
    #: ``equipment.yaml`` id this place belongs to; required for ``deck``.
    equipment: str | None = None
    #: How many containers the place holds at once. Informational — surfaced
    #: as a dashboard warning, never enforced by the ledger (refusing a
    #: truthful record is worse than a visible double-occupancy).
    capacity: int | None = Field(default=1, ge=1)
    #: Observation-only device vocabulary, keyed by equipment id: the token(s)
    #: that device uses for this place (an OT-2 slot key ``"2"``, xArm graph
    #: nodes ``["opentrons_2_low", "opentrons_2_high"]``).
    aliases: dict[str, str | list[str]] = Field(default_factory=dict)
    #: Human label for UIs.
    label: str | None = None
    #: Removal = ``active: false``; entries are never deleted.
    active: bool = True
    #: Free-form notes (e.g. "confirm with lab") — not contract.
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def _name_shape(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError(
                f"location name {v!r} must be a slash-path of lowercase "
                "snake_case segments, e.g. 'ot2_hte/slot_2' or 'bench/hte_staging'"
            )
        return v

    def alias_tokens(self, equipment_id: str) -> list[str]:
        """The alias tokens this place carries for ``equipment_id`` (maybe empty)."""
        raw = self.aliases.get(equipment_id)
        if raw is None:
            return []
        return [raw] if isinstance(raw, str) else list(raw)


class LocationsConfig(BaseModel):
    """Parsed ``locations.yaml``."""

    locations: list[LocationEntry]

    def names(self) -> list[str]:
        return [loc.name for loc in self.locations]

    def by_name(self, name: str) -> LocationEntry | None:
        for loc in self.locations:
            if loc.name == name:
                return loc
        return None

    def for_equipment(self, equipment_id: str) -> list[LocationEntry]:
        """Places anchored to ``equipment_id`` (by ``equipment``), in file order."""
        return [loc for loc in self.locations if loc.equipment == equipment_id]

    def resolve_alias(self, equipment_id: str, token: str) -> str | None:
        """Canonical location name for a device's own token, or ``None``.

        Read-side only: turns ``("ot2_hte", "2")`` or
        ``("xarm_translocation", "opentrons_2_low")`` into ``"ot2_hte/slot_2"``
        so a device snapshot can be compared against the ledger. It must never
        be used to *write* a move — custody is declared, not inferred.
        """
        for loc in self.locations:
            if token in loc.alias_tokens(equipment_id):
                return loc.name
        return None

    def validate_against(self, registry: Registry) -> list[str]:
        """Cross-file checks that pydantic cannot do alone. Returns problems.

        Pure: the dashboard logs these at startup, a test asserts the list is
        empty for the committed files. Checks:

        - every ``equipment`` names an entry in ``equipment.yaml``
        - ``deck`` places carry an ``equipment``
        - a device-anchored name is prefixed by its own equipment id
        - names are unique
        - alias tokens are unique per equipment (one token → one place)
        """
        problems: list[str] = []
        known = {e.id for e in registry.equipment}
        seen_names: set[str] = set()
        seen_aliases: dict[tuple[str, str], str] = {}
        for loc in self.locations:
            if loc.name in seen_names:
                problems.append(f"duplicate location name {loc.name!r}")
            seen_names.add(loc.name)
            if loc.equipment is not None and loc.equipment not in known:
                problems.append(
                    f"{loc.name}: equipment {loc.equipment!r} is not in equipment.yaml"
                )
            if loc.type == "deck" and loc.equipment is None:
                problems.append(f"{loc.name}: a 'deck' location must name its equipment")
            if loc.equipment is not None and not loc.name.startswith(loc.equipment + "/"):
                problems.append(
                    f"{loc.name}: device-anchored names must be prefixed by their "
                    f"equipment id ({loc.equipment}/…)"
                )
            for eq_id, tokens in loc.aliases.items():
                if eq_id not in known:
                    problems.append(
                        f"{loc.name}: alias key {eq_id!r} is not in equipment.yaml"
                    )
                for tok in ([tokens] if isinstance(tokens, str) else tokens):
                    key = (eq_id, tok)
                    if key in seen_aliases and seen_aliases[key] != loc.name:
                        problems.append(
                            f"alias {tok!r} of {eq_id} points at both "
                            f"{seen_aliases[key]!r} and {loc.name!r}"
                        )
                    seen_aliases[key] = loc.name
        return problems


def _default_locations_path() -> Path:
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "locations.yaml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not locate locations.yaml in any ancestor directory of "
        f"{here}; pass an explicit path to load_locations() or set "
        "LAB_LOCATIONS_PATH."
    )


def load_locations(path: str | os.PathLike | None = None) -> LocationsConfig:
    """Load the location registry from YAML.

    Path resolution order:

    1. Argument ``path``, if provided.
    2. ``LAB_LOCATIONS_PATH`` environment variable.
    3. The first ``locations.yaml`` found by walking up from this module.
    """

    resolved: Path
    if path is not None:
        resolved = Path(path)
    elif os.environ.get("LAB_LOCATIONS_PATH"):
        resolved = Path(os.environ["LAB_LOCATIONS_PATH"])
    else:
        resolved = _default_locations_path()

    if not resolved.exists():
        raise FileNotFoundError(f"Locations config not found at {resolved}")

    with resolved.open("r") as f:
        data = yaml.safe_load(f) or {}

    try:
        return LocationsConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid locations config at {resolved}: {exc}"
        ) from exc
