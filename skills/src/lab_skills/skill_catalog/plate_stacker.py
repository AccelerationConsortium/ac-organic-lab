"""Skill catalog entries for ``kind=plate_stacker``.

Reference device: :mod:`agilent-biostack4-standalone` (Agilent BioStack 4),
on STATUS_SPEC v1.1 with a claim-gated ``/control/*`` surface. All control
calls require an ``X-Claim-Token`` header obtained via ``POST /control/claim``.

Device endpoints
----------------
* ``POST /control/startup``        Connect + home to a known state (requires_init)
* ``POST /control/shutdown``       Disconnect the stacker
* ``POST /control/home``           Re-home the transfer/elevator mechanism
* ``POST /control/stage_plate``    Pick the next plate off the stack onto the shuttle
* ``POST /control/present_plate``  Present the staged plate to the handoff position
* ``POST /control/handoff``        Release the staged plate to the downstream device

``allowed_actions`` mapping (device-authoritative once v1.1)
------------------------------------------------------------
* ``requires_init``  →  ``["startup"]``
* ``ready``          →  ``["shutdown", "home", "stage_plate", "present_plate", "handoff"]``

The ``requires_states`` below are the v1.0-style fallback; live availability
comes from the device's ``allowed_actions``. ``equipment.yaml`` keeps
``do_not_call_connect: true``, so the SDK never auto-connects.
"""

from __future__ import annotations

from pydantic import BaseModel

from .models import SkillDef
from .registry import register


class StartupArgs(BaseModel):
    """Body for ``POST /control/startup`` (no parameters)."""


class ShutdownArgs(BaseModel):
    """Body for ``POST /control/shutdown`` (no parameters)."""


class HomeArgs(BaseModel):
    """Body for ``POST /control/home`` (no parameters)."""


class StagePlateArgs(BaseModel):
    """Body for ``POST /control/stage_plate`` (no parameters)."""


class PresentPlateArgs(BaseModel):
    """Body for ``POST /control/present_plate`` (no parameters)."""


class HandoffArgs(BaseModel):
    """Body for ``POST /control/handoff`` (no parameters)."""


register(
    "plate_stacker",
    [
        SkillDef(
            name="startup",
            kind="plate_stacker",
            description=(
                "Connect to the stacker and home it to a known state. Required "
                "before any plate movement on a freshly booted device."
            ),
            endpoint="/control/startup",
            args_schema=StartupArgs,
            requires_states=["requires_init", "dry_run"],
            estimated_duration_s=8.0,
        ),
        SkillDef(
            name="shutdown",
            kind="plate_stacker",
            description="Disconnect the stacker; a startup is required afterwards.",
            endpoint="/control/shutdown",
            args_schema=ShutdownArgs,
            requires_states=["ready", "busy", "degraded", "error", "dry_run"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="home",
            kind="plate_stacker",
            description="Re-home the transfer/elevator mechanism to a known position.",
            endpoint="/control/home",
            args_schema=HomeArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=6.0,
        ),
        SkillDef(
            name="stage_plate",
            kind="plate_stacker",
            description="Pick the next plate off the stack onto the shuttle.",
            endpoint="/control/stage_plate",
            args_schema=StagePlateArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=5.0,
        ),
        SkillDef(
            name="present_plate",
            kind="plate_stacker",
            description="Present the staged plate to the handoff position.",
            endpoint="/control/present_plate",
            args_schema=PresentPlateArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=4.0,
        ),
        SkillDef(
            name="handoff",
            kind="plate_stacker",
            description="Release the staged plate to the downstream device.",
            endpoint="/control/handoff",
            args_schema=HandoffArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=4.0,
        ),
    ],
)


__all__ = [
    "HandoffArgs",
    "HomeArgs",
    "PresentPlateArgs",
    "ShutdownArgs",
    "StagePlateArgs",
    "StartupArgs",
]
