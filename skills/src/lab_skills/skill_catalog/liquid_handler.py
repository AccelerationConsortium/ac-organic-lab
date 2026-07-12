"""Skill catalog entries for ``kind=liquid_handler``.

Reference device: ``opentrons-server`` (the OT-2 gateway on
``sdl2-pc-03-cytation:8020`` and the complexation OT-2 on ``:8021``).

This module catalogs the OT-2's full ``/control/*`` surface with typed
Pydantic ``args_schema``s:

* **Session lifecycle** — ``startup`` / ``shutdown``.
* **Protocol execution** — ``setup`` / ``home`` / ``pick_up_tip`` /
  ``aspirate`` / ``dispense`` / ``drop_tip`` / ``move_labware`` /
  ``pause`` / ``resume``.
* **Plate / well tracking** — ``plate.load`` / ``plate.unload`` /
  ``well.update`` (orchestrator-owned per-well state).
* **Convenience** — ``lights.set`` (deck light) and ``deck.declare``
  (operator/recipe deck layout, metadata only).

Each ``name`` matches, byte-for-byte, a string the gateway advertises in
``EquipmentStatus.allowed_actions`` (see ``opentrons-server``
``gateway/service.py::allowed_actions``). The SDK computes availability as
``def.name in allowed_actions`` (``session.py::_availability``), so these
names are the contract — renaming one breaks ``lab.skills()`` matching and is
a breaking change (``SKILLS_CATALOG.md`` §versioning).

``startup`` opens the OT-2's SSH / run-engine session. It is a real, listed
skill (advertised in ``requires_init``), but the SDK must **never** call it
automatically — the ``ot2`` / ``ot2_complexation`` registry entries carry
``do_not_call_connect: true``. Auto-connect and cataloging are separate
concerns; listing the skill lets an operator/workflow invoke it explicitly.

``lights.set`` is intentionally *not* behind ``CONTROL_PASSWORD`` per
``web/src/lib/tile-policy.ts`` — same class as camera PTZ and "light" power
outlets. The remaining actions drive hardware and stay password-gated.

The gateway's ``reconcile`` verb is deliberately **not** cataloged: it is an
operator recovery hook (acknowledge an ``unknown_outcome``) that the device
never lists in ``allowed_actions``, so a SkillDef for it would always report
``available=False``.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .models import SkillDef
from .registry import register


# ---------------------------------------------------------------------------
# Argument schemas — mirror opentrons-server ``gateway/models.py`` (the SDK
# does not import the device repo, so the shapes are vendored, like every
# other kind's catalog). Kept permissive (``extra="allow"``) on the setup
# sub-objects because custom labware / instruments carry a free-form ``config``.
# ---------------------------------------------------------------------------


class _NoArgs(BaseModel):
    """Empty body for parameter-less actions (home / shutdown / pause /
    resume / plate.unload)."""


class LightsSetArgs(BaseModel):
    """Body for ``POST /control/lights`` (mirrors Opentrons ``POST /robot/lights``)."""

    on: bool


class DeckDeclareArgs(BaseModel):
    """Body for ``POST /control/deck/declare``.

    Sets the operator/recipe-declared deck layout on the OT-2 gateway (the
    source of truth that retires the dashboard's ``deck_layouts.json`` stopgap).
    Each slot value is a labware ``load_name`` (preferred), a bare ``kind``
    string (e.g. ``"96-well"``, ``"tiprack"``, ``"waste"``), a
    ``{"load_name"|"kind": ...}`` object, or ``null`` to clear that slot. An
    empty ``slots`` map clears the whole declaration.
    """

    slots: Dict[str, Optional[Union[str, Dict[str, str]]]] = Field(default_factory=dict)


class StartupArgs(BaseModel):
    """Body for ``POST /control/startup``.

    ``host_alias`` / ``password`` are optional overrides; omit both so the
    gateway uses its own ``$OT2_HOST_ALIAS`` / ``$OT2_SSH_PASSWORD`` service
    env (never bake a device secret into a workflow). ``simulation=True``
    connects/validates without moving liquid.
    """

    simulation: bool = False
    host_alias: Optional[str] = None
    password: Optional[str] = None


class LabwareSpec(BaseModel):
    """One entry of ``setup.labware``. ``loadname`` is required when
    ``ot_default`` is True; ``config`` (a full Opentrons labware-definition
    JSON) is required when it is False (custom labware)."""

    model_config = ConfigDict(extra="allow")

    nickname: str
    location: str
    ot_default: bool = True
    loadname: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class InstrumentSpec(BaseModel):
    """One entry of ``setup.instruments``. ``instrument_name`` (e.g.
    ``p300_multi_gen2``) is required when ``ot_default`` is True."""

    model_config = ConfigDict(extra="allow")

    nickname: str
    mount: Literal["left", "right"]
    ot_default: bool = True
    instrument_name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class ModuleSpec(BaseModel):
    """One entry of ``setup.modules`` (temperature / magnetic / heater-shaker)."""

    model_config = ConfigDict(extra="allow")

    nickname: str
    module_name: str
    location: str


class SetupArgs(BaseModel):
    """Body for ``POST /control/setup`` — load pipettes, tipracks, plates,
    reservoirs, and modules under stable nicknames the other verbs reference."""

    labware: list[LabwareSpec] = Field(default_factory=list)
    instruments: list[InstrumentSpec] = Field(default_factory=list)
    modules: list[ModuleSpec] = Field(default_factory=list)


class WellLocation(BaseModel):
    """A well on a loaded labware, with an optional vertical offset (mm).

    Give at most one of ``top`` / ``bottom`` (offset from that reference);
    ``center=True`` aspirates/dispenses at the well centre.
    """

    labware_nickname: str
    position: str = Field(..., description='well name, e.g. "A1" / "H12"')
    top: Optional[float] = None
    bottom: Optional[float] = None
    center: bool = False


class LiquidMoveArgs(BaseModel):
    """Body for ``POST /control/aspirate`` and ``POST /control/dispense``.

    ``flow_rate`` (µL/s) is optional: omit to use the transport default (the
    pipette's protocol-API default on SSH; the ``OT2_HTTP_*_FLOW_UL_S`` env
    default on the run-engine HTTP transport, which has no implicit default).
    """

    pipette: str
    volume_ul: float = Field(..., gt=0.0)
    location: WellLocation
    flow_rate: Optional[float] = Field(default=None, gt=0.0)


class TipArgs(BaseModel):
    """Body for ``POST /control/pick-up-tip`` and ``POST /control/drop-tip``.

    On ``pick_up_tip`` pass ``labware_nickname`` (a loaded tiprack) +
    ``position`` (its well) to name the exact tip — the HTTP run-engine
    transport requires it (the SSH transport picks the next tip when omitted).
    On ``drop_tip`` those name an explicit drop well (e.g. return the tip to
    the rack); omit both to drop in the gateway's default trash.
    """

    pipette: str
    labware_nickname: Optional[str] = None
    position: Optional[str] = None


class MoveLabwareArgs(BaseModel):
    """Body for ``POST /control/move-labware``.

    ``new_location`` is an OT-2 deck slot (``"1"``..``"12"``) or ``"OFF_DECK"``.
    On the OT-2 this records the move (bookkeeping); it does not drive a
    gripper. Home the gantry before an arm/hand enters the deck — the gateway
    will not retract for you (see ``opentrons-server`` ``HTTP_DRIVE_PLAN.md``).
    """

    labware_nickname: str
    new_location: str = Field(..., description='deck slot "1".."12" or "OFF_DECK"')


class PlateLoadArgs(BaseModel):
    """Body for ``POST /control/plate/load`` — register the plate the
    orchestrator considers loaded. ``model`` is an Opentrons ``load_name``
    (no gateway default); ``wells`` defaults to 96 empty wells."""

    plate_id: str = Field(..., min_length=1, max_length=128)
    model: str = Field(..., min_length=1)
    wells: Optional[list[Dict[str, Any]]] = None


class WellUpdateArgs(BaseModel):
    """Body for ``POST /control/well/update`` — mutate one well of the loaded
    plate (the device-owned ``volume_ul`` plus orchestrator-owned metadata)."""

    well: str = Field(..., min_length=2, max_length=3, description="e.g. A1, H12")
    sample_id: Optional[str] = None
    volume_ul: Optional[float] = Field(default=None, ge=0.0)
    notes: Optional[str] = None
    clear_sample_id: bool = False
    clear_notes: bool = False


register(
    "liquid_handler",
    [
        # ---- session lifecycle ------------------------------------------
        SkillDef(
            name="startup",
            kind="liquid_handler",
            description=(
                "Open the OT-2 session (SSH / run-engine) and protocol context. "
                "~60 s on real hardware. Never auto-called by the SDK "
                "(registry do_not_call_connect); explicit invocation only."
            ),
            endpoint="/control/startup",
            args_schema=StartupArgs,
            requires_states=["requires_init", "dry_run", "error"],
            estimated_duration_s=60.0,
        ),
        SkillDef(
            name="shutdown",
            kind="liquid_handler",
            description="Close the OT-2 protocol context / session.",
            endpoint="/control/shutdown",
            args_schema=_NoArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=5.0,
        ),
        # ---- protocol execution -----------------------------------------
        SkillDef(
            name="setup",
            kind="liquid_handler",
            description=(
                "Load labware, pipettes, and modules under nicknames the other "
                "verbs reference. Supports Opentrons defaults and custom "
                "labware/instrument definitions."
            ),
            endpoint="/control/setup",
            args_schema=SetupArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=15.0,
        ),
        SkillDef(
            name="home",
            kind="liquid_handler",
            description="Home the gantry to a safe pose. Do this before an arm/hand enters the deck.",
            endpoint="/control/home",
            args_schema=_NoArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=10.0,
        ),
        SkillDef(
            name="pick_up_tip",
            kind="liquid_handler",
            description=(
                "Pick up a tip. Pass labware_nickname + position for an explicit "
                "tip (required on the HTTP transport)."
            ),
            endpoint="/control/pick-up-tip",
            args_schema=TipArgs,
            requires_states=["ready"],
            estimated_duration_s=4.0,
        ),
        SkillDef(
            name="aspirate",
            kind="liquid_handler",
            description="Aspirate a volume from a well (optional flow_rate in µL/s).",
            endpoint="/control/aspirate",
            args_schema=LiquidMoveArgs,
            requires_states=["ready"],
            estimated_duration_s=3.0,
        ),
        SkillDef(
            name="dispense",
            kind="liquid_handler",
            description="Dispense a volume into a well (optional flow_rate in µL/s).",
            endpoint="/control/dispense",
            args_schema=LiquidMoveArgs,
            requires_states=["ready"],
            estimated_duration_s=3.0,
        ),
        SkillDef(
            name="drop_tip",
            kind="liquid_handler",
            description=(
                "Drop a tip. Omit the location for the default trash, or pass "
                "labware_nickname + position to return it to a specific well."
            ),
            endpoint="/control/drop-tip",
            args_schema=TipArgs,
            requires_states=["ready"],
            estimated_duration_s=3.0,
        ),
        SkillDef(
            name="move_labware",
            kind="liquid_handler",
            description=(
                "Record a labware move to a deck slot or OFF_DECK (bookkeeping; "
                "no gripper on the OT-2). Home before any physical handoff."
            ),
            endpoint="/control/move-labware",
            args_schema=MoveLabwareArgs,
            requires_states=["ready"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="pause",
            kind="liquid_handler",
            description="Pause the OT-2 (housekeeping).",
            endpoint="/control/pause",
            args_schema=_NoArgs,
            requires_states=["ready", "busy"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="resume",
            kind="liquid_handler",
            description="Resume a paused OT-2.",
            endpoint="/control/resume",
            args_schema=_NoArgs,
            # Paused maps to STATUS_SPEC "degraded" on this gateway.
            requires_states=["degraded"],
            estimated_duration_s=1.0,
        ),
        # ---- plate / well tracking (orchestrator-owned state) -----------
        SkillDef(
            name="plate.load",
            kind="liquid_handler",
            description=(
                "Register the plate currently loaded on the deck, hydrating its "
                "per-well state. Surfaces on /status details.loaded_plate."
            ),
            endpoint="/control/plate/load",
            args_schema=PlateLoadArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="plate.unload",
            kind="liquid_handler",
            description="Unload the tracked plate; returns its last per-well state.",
            endpoint="/control/plate/unload",
            args_schema=_NoArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=2.0,
        ),
        SkillDef(
            name="well.update",
            kind="liquid_handler",
            description="Update one well of the loaded plate (volume / sample_id / notes).",
            endpoint="/control/well/update",
            args_schema=WellUpdateArgs,
            requires_states=["ready", "dry_run"],
            estimated_duration_s=0.2,
        ),
        # ---- convenience -------------------------------------------------
        SkillDef(
            name="lights.set",
            kind="liquid_handler",
            description="Turn the OT-2 deck lights on or off. Convenience control.",
            endpoint="/control/lights",
            args_schema=LightsSetArgs,
            # Empty: the device exposes lights.set in allowed_actions
            # regardless of equipment_status. Don't gate on the SDK side
            # either — the operator might want lights on while the robot
            # is still in requires_init.
            requires_states=[],
            estimated_duration_s=0.2,
        ),
        SkillDef(
            name="deck.declare",
            kind="liquid_handler",
            description=(
                "Declare the deck layout (operator/recipe intent). Metadata only "
                "— no hardware motion. Merged with observed sources on /status."
            ),
            endpoint="/control/deck/declare",
            args_schema=DeckDeclareArgs,
            # Like lights.set: the gateway advertises deck.declare in
            # allowed_actions whenever it is reachable (any state except
            # EXTERNAL_CONTROL), so declaring works even in requires_init.
            requires_states=[],
            estimated_duration_s=0.2,
        ),
    ],
)


__all__ = [
    "DeckDeclareArgs",
    "InstrumentSpec",
    "LabwareSpec",
    "LightsSetArgs",
    "LiquidMoveArgs",
    "ModuleSpec",
    "MoveLabwareArgs",
    "PlateLoadArgs",
    "SetupArgs",
    "StartupArgs",
    "TipArgs",
    "WellLocation",
    "WellUpdateArgs",
]
