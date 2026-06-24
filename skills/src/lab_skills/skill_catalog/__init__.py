"""Skill catalog: static :class:`SkillDef` registry + runtime :class:`Skill` model.

This subpackage is the SDK's hand-curated description of "what every kind of
equipment in the lab can do". Per ``docs/SKILLS_CATALOG.md`` the data shapes
are:

* :class:`SkillDef` - one entry per ``(kind, action)`` pair, registered into
  the process-wide :data:`SKILL_REGISTRY` at import time. Independent of any
  running device.
* :class:`Skill` - runtime view returned by ``await session.skills()`` that
  binds a :class:`SkillDef` to a project role and a live availability flag.

Adding a new equipment kind is exactly one new module under ``skill_catalog/`` that
imports :func:`register` and calls it with a list of :class:`SkillDef`s.

Importing this package (``from lab_skills.skill_catalog import ...``) is
sufficient to populate the registry; the per-kind modules are imported here
purely for their side effects.
"""

from __future__ import annotations

from .models import Skill, SkillDef
from .registry import SKILL_REGISTRY, register, skills_for

# Eager imports so that consumers of the catalog see a fully populated
# SKILL_REGISTRY without having to know which kind modules exist. Each
# module's top-level ``register(...)`` call runs at this point.
from . import fume_hood as _fume_hood  # noqa: F401
from . import hplc as _hplc  # noqa: F401
from . import liquid_handler as _liquid_handler  # noqa: F401
from . import plate_reader as _plate_reader  # noqa: F401
from . import plate_sealer as _plate_sealer  # noqa: F401
from . import plate_stacker as _plate_stacker  # noqa: F401
from . import press as _press  # noqa: F401
from . import robot_arm as _robot_arm  # noqa: F401
from . import shaker as _shaker  # noqa: F401
from . import solid_doser as _solid_doser  # noqa: F401

__all__ = [
    "SKILL_REGISTRY",
    "Skill",
    "SkillDef",
    "register",
    "skills_for",
]
