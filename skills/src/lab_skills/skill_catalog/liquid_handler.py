"""Skill catalog entries for ``kind=liquid_handler``.

Reference device: ``opentrons-server`` (the OT-2 gateway on
``sdl2-pc-03-cytation:8020`` and the complexation OT-2 on ``:8021``).

This module catalogs the typed union of the OT-2 and Flex plan surfaces with
Pydantic ``args_schema``s:

* **Session lifecycle** — ``startup`` / ``shutdown``.
* **Protocol execution** — ``setup`` / ``home`` / ``pick_up_tip`` /
  ``aspirate`` / ``dispense`` / ``drop_tip`` / ``move_labware`` /
  ``pause`` / ``resume``.
* **Plate / well tracking** — ``plate.load`` / ``plate.unload`` /
  ``well.update`` (orchestrator-owned per-well state).
* **Tip tracking** — ``tips.reset`` (whole-rack swap) and ``tips.mark``
  (per-well / per-column correction); metadata only.
* **Temperature module** — ``tempmod.set`` / ``tempmod.deactivate``.
* **Convenience** — ``lights.set`` (deck light) and ``deck.declare``
  (operator/recipe deck layout, metadata only).

Each ``name`` matches, byte-for-byte, a string a gateway profile publishes in
``GET /plans/actions`` and may advertise in ``EquipmentStatus.allowed_actions``.
Runtime discovery selects the model-specific subset. The SDK computes availability as
``def.name in allowed_actions`` (``session.py::_availability``), so these
names are the contract — renaming one breaks ``lab.skills()`` matching and is
a breaking change (``SKILLS_CATALOG.md`` §versioning).

``startup`` opens the OT-2's SSH / run-engine session. It is a real, listed
skill (advertised in ``requires_init``), but the SDK must **never** call it
automatically — the ``ot2_hte`` / ``ot2_complexation`` registry entries carry
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

import re

from typing import Annotated, Any, Dict, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import SkillDef
from .registry import register


MAX_PIPETTE_VOLUME_UL = 1000.0
MAX_WELL_OFFSET_MM = 100.0
MAX_FLEX_X_MM = 477.2
MAX_FLEX_Y_MM = 493.8
MAX_Z_MM = 218.0
ROBOT_REFERENCE_PATTERN = r"^[A-Za-z0-9_-]+$"
WELL_NAME_PATTERN = r"^[A-Z]+[1-9][0-9]*$"


# ---------------------------------------------------------------------------
# Argument schemas — mirror opentrons-server ``gateway/models.py`` (the SDK
# does not import the device repo, so the shapes are vendored, like every
# other kind's catalog). Kept permissive (``extra="allow"``) on the setup
# sub-objects because custom labware / instruments carry a free-form ``config``.
# ---------------------------------------------------------------------------


class _StrictArgs(BaseModel):
    """Gateway request bodies reject unknown keys and non-finite numbers."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _NoArgs(_StrictArgs):
    """Empty body for parameter-less actions (home / shutdown / pause /
    resume / plate.unload)."""


class LightsSetArgs(_StrictArgs):
    """Body for ``POST /control/lights`` (mirrors Opentrons ``POST /robot/lights``)."""

    on: bool


class DeckDeclareArgs(_StrictArgs):
    """Body for ``POST /control/deck/declare``.

    Sets the operator/recipe-declared deck layout on the OT-2 gateway (the
    source of truth that retires the dashboard's ``deck_layouts.json`` stopgap).
    Each slot value is a labware ``load_name`` (preferred), a bare ``kind``
    string (e.g. ``"96-well"``, ``"tiprack"``, ``"waste"``), a
    ``{"load_name"|"kind": ...}`` object, or ``null`` to clear that slot. An
    empty ``slots`` map clears the whole declaration.

    A labware object may also carry ``definition`` — a full Opentrons schema-2
    labware definition — alongside ``load_name``: ``{"load_name": ...,
    "definition": {...}}``. When present, the gateway derives ``kind`` /
    ``rows`` / ``columns`` / ``is_tiprack`` from the real geometry instead of
    guessing from ``load_name`` alone (``opentrons-server``
    ``gateway/models.py::DeckDeclareRequest``). This is required for any
    custom labware whose ``load_name`` doesn't parse via the gateway's
    ``classify_labware`` regex — without it, the slot silently degrades to
    ``kind: "unknown"`` with no grid. The value type below is ``Dict[str,
    Any]`` (not ``Dict[str, str]``) specifically so ``definition`` — a nested
    object — round-trips; a narrower type here previously made it impossible
    to send a real definition through this skill at all.
    """

    slots: Dict[str, Optional[Union[str, Dict[str, Any]]]] = Field(default_factory=dict)


class StartupArgs(_StrictArgs):
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
    location: str = Field(
        ...,
        description=(
            'deck slot as the bare key, "1".."11" — never a registry name '
            "(ot2_hte/slot_2) or an xArm node id (opentrons_2_low), which the "
            "gateway refuses"
        ),
    )
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

    @model_validator(mode="after")
    def _supported_flex_head(self) -> "InstrumentSpec":
        name = self.instrument_name or ""
        if name.startswith("flex_") and not re.fullmatch(r"flex_(1|8)channel_(50|1000)", name):
            raise ValueError(
                "Flex currently supports full 1/8-channel heads only; "
                "96-channel and partial layouts are not supported"
            )
        return self


class ModuleSpec(BaseModel):
    """One entry of ``setup.modules`` (temperature / magnetic / heater-shaker)."""

    model_config = ConfigDict(extra="allow")

    nickname: str
    module_name: str
    location: str


class SetupArgs(_StrictArgs):
    """Body for ``POST /control/setup`` — load pipettes, tipracks, plates,
    reservoirs, and modules under stable nicknames the other verbs reference."""

    labware: list[LabwareSpec] = Field(default_factory=list)
    instruments: list[InstrumentSpec] = Field(default_factory=list)
    modules: list[ModuleSpec] = Field(default_factory=list)


class WellLocation(_StrictArgs):
    """A well on a loaded labware, with an optional vertical offset (mm).

    Give at most one of ``top`` / ``bottom`` (offset from that reference);
    ``center=True`` aspirates/dispenses at the well centre.
    """

    labware_nickname: str = Field(pattern=ROBOT_REFERENCE_PATTERN)
    position: str = Field(
        ..., pattern=WELL_NAME_PATTERN, description='well name, e.g. "A1" / "H12"'
    )
    top: Optional[float] = Field(default=None, ge=-MAX_WELL_OFFSET_MM, le=MAX_WELL_OFFSET_MM)
    bottom: Optional[float] = Field(default=None, ge=-MAX_WELL_OFFSET_MM, le=MAX_WELL_OFFSET_MM)
    center: bool = False

    @model_validator(mode="after")
    def _one_reference(self) -> "WellLocation":
        if sum((self.top is not None, self.bottom is not None, self.center)) > 1:
            raise ValueError("provide only one of top, bottom or center")
        return self


class LiquidMoveArgs(_StrictArgs):
    """Body for ``POST /control/aspirate`` and ``POST /control/dispense``.

    ``flow_rate`` (µL/s) is optional: omit to use the transport default (the
    pipette's protocol-API default on SSH; the ``OT2_HTTP_*_FLOW_UL_S`` env
    default on the run-engine HTTP transport, which has no implicit default).
    """

    pipette: str = Field(pattern=ROBOT_REFERENCE_PATTERN)
    volume_ul: float = Field(..., gt=0.0, le=MAX_PIPETTE_VOLUME_UL)
    location: WellLocation
    flow_rate: Optional[float] = Field(default=None, gt=0.0)


class DispenseArgs(LiquidMoveArgs):
    """Dispense plus the HTTP run engine's optional plunger-air push-out."""

    push_out: Optional[float] = Field(default=None, ge=0, le=MAX_PIPETTE_VOLUME_UL)


class CoordinateLocation(_StrictArgs):
    """Absolute deck coordinates in mm (the robot's deck reference frame)."""

    # This static catalog spans OT-2 and Flex. The running gateway's OpenAPI
    # supplies its tighter model-specific X/Y bounds to discovery clients.
    x: float = Field(ge=0, le=MAX_FLEX_X_MM)
    y: float = Field(ge=0, le=MAX_FLEX_Y_MM)
    z: float = Field(ge=0, le=MAX_Z_MM)


class MoveToArgs(_StrictArgs):
    """Body for ``POST /control/move-to`` (pipette motion, no liquid).

    Exactly one of ``location`` (well-addressed) or ``coordinates`` (absolute
    deck frame, mm) must be given; the gateway 422s otherwise. ``force_direct``
    moves in a straight line instead of the arced safe path — the caller owns
    collision avoidance when set.
    """

    pipette: str = Field(pattern=ROBOT_REFERENCE_PATTERN)
    location: Optional[WellLocation] = None
    coordinates: Optional[CoordinateLocation] = None
    speed: Optional[float] = Field(default=None, gt=0.0, description="mm/s")
    force_direct: bool = False
    minimum_z_height: Optional[float] = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "MoveToArgs":
        if (self.location is None) == (self.coordinates is None):
            raise ValueError("provide exactly one of 'location' or 'coordinates'")
        return self


class TipArgs(_StrictArgs):
    """Body for ``POST /control/pick-up-tip`` and ``POST /control/drop-tip``.

    On ``pick_up_tip`` pass ``labware_nickname`` (a loaded tiprack) +
    ``position`` (its well) to name the exact tip — the HTTP run-engine
    transport requires it. On the SSH transport, omitting ``position`` on a
    tracked rack auto-picks the next available tip (column-major). On
    ``drop_tip`` those name an explicit drop well (e.g. return the tip to
    the rack); omit both to drop in the gateway's default trash.

    ``sample_id`` / ``force`` drive the gateway's cross-contamination guard
    on ``pick_up_tip``: a fresh tip is always free, a tip that previously
    touched the same ``sample_id`` is reusable, and ``force`` overrides the
    guard (never an empty well). Refusals return HTTP 412 with a structured
    body (STATUS_SPEC §6.1) and never mutate ``last_error`` (§6.3).
    """

    pipette: str = Field(pattern=ROBOT_REFERENCE_PATTERN)
    labware_nickname: Optional[str] = Field(default=None, pattern=ROBOT_REFERENCE_PATTERN)
    position: Optional[str] = Field(default=None, pattern=WELL_NAME_PATTERN)
    sample_id: Optional[str] = None
    force: bool = False


class TipsResetArgs(_StrictArgs):
    """Body for ``POST /control/tips/reset`` — (re)register a tip rack with
    every tip fresh, marking a physical rack swap. Racks named in
    ``/control/setup`` labware register automatically and keep their used-tip
    statuses across restarts; this endpoint is for swapping in a fresh rack or
    tracking one loaded out-of-band. Metadata only — no robot motion — so,
    like ``plate.*``, it works in ``ready`` and ``dry_run``. ``wells`` defaults
    to the 96-tip column-major grid when omitted.

    Address the rack the same way ``tips.mark`` does (mirrors
    ``opentrons-server`` ``TipsResetRequest``): by ``slot`` — the deck slot,
    which *is* a rack's identity, since a rack carries no sample and what an
    operator refills is "the rack in slot 4" — or by ``nickname``, the name it
    was loaded under, kept as a legacy alias and resolved through the session
    recipe. At least one is required. This args model was nickname-only while
    the gateway had already made ``slot`` the preferred form, so the SDK could
    not express the call the device prefers.
    """

    slot: Optional[str] = Field(
        default=None,
        min_length=1,
        description='deck slot holding the rack, e.g. "5" (preferred)',
    )
    nickname: Optional[str] = Field(
        default=None,
        min_length=1,
        description="name the rack was loaded under (legacy alias for slot)",
    )
    wells: Optional[list[str]] = None

    @model_validator(mode="after")
    def _validate_target(self) -> "TipsResetArgs":
        if not self.slot and not self.nickname:
            raise ValueError("Give slot (preferred) or nickname to name the tip rack.")
        return self


class TipsMarkArgs(_StrictArgs):
    """Body for ``POST /control/tips/mark`` — set the status of *part* of a
    tracked rack (mirrors ``opentrons-server`` ``TipsMarkRequest``).

    Address the rack by ``slot`` (preferred — the deck slot it sits in) or by
    ``nickname`` (the legacy alias, the name it was loaded under); at least one
    is required. Address the tips by **exactly one** of ``wells`` (e.g.
    ``["A1", "B1"]``) or ``columns`` (1-12); supplying both, or neither, is an
    error. ``status`` says what is actually in those positions: ``"new"`` for a
    fresh tip, ``"empty"`` for a used-or-absent one.

    Metadata only — no robot motion — so, like ``tips.reset`` and ``plate.*``,
    it works in ``ready`` and ``dry_run``.
    """

    slot: Optional[str] = Field(
        default=None,
        min_length=1,
        description='deck slot holding the rack, e.g. "5" (preferred)',
    )
    nickname: Optional[str] = Field(
        default=None,
        min_length=1,
        description="name the rack was loaded under (legacy alias for slot)",
    )
    wells: Optional[list[str]] = Field(
        default=None,
        description='tip wells to mark, e.g. ["A1", "B1"]; give this or columns, not both',
    )
    columns: Optional[list[Annotated[int, Field(ge=1, le=12)]]] = Field(
        default=None,
        description="whole tip columns to mark, 1-12; give this or wells, not both",
    )
    status: Literal["new", "empty"] = Field(
        ...,
        description='"new" = a fresh tip is there, "empty" = used or absent',
    )

    @model_validator(mode="after")
    def _validate_target(self) -> "TipsMarkArgs":
        if not self.slot and not self.nickname:
            raise ValueError("Give slot (preferred) or nickname to name the tip rack.")
        if bool(self.wells) == bool(self.columns):
            raise ValueError("Give exactly one of wells or columns.")
        return self


class TempmodSetArgs(_StrictArgs):
    """Body for ``POST /control/tempmod/set`` — target temperature for an
    Opentrons temperature module on the deck."""

    celsius: float = Field(
        ...,
        ge=4.0,
        le=95.0,
        description="target block temperature, 4-95 °C (the module's range)",
    )
    module: Optional[str] = Field(
        default=None,
        description=(
            "which temperature module: its recipe nickname or its deck slot. "
            "Omit when exactly one temperature module is on the deck — the "
            "gateway resolves it."
        ),
    )


class TempmodDeactivateArgs(_StrictArgs):
    """Body for ``POST /control/tempmod/deactivate`` — stop holding a
    temperature and let the block drift to ambient."""

    module: Optional[str] = Field(
        default=None,
        description=(
            "which temperature module: its recipe nickname or its deck slot. "
            "Omit when exactly one temperature module is on the deck."
        ),
    )


class GripperOffset(_StrictArgs):
    """Flex gripper pickup/drop offset in millimetres; explicit zero is kept."""

    x: float = Field(default=0, ge=-MAX_WELL_OFFSET_MM, le=MAX_WELL_OFFSET_MM)
    y: float = Field(default=0, ge=-MAX_WELL_OFFSET_MM, le=MAX_WELL_OFFSET_MM)
    z: float = Field(default=0, ge=-MAX_WELL_OFFSET_MM, le=MAX_WELL_OFFSET_MM)


class MoveLabwareArgs(_StrictArgs):
    """Body for ``POST /control/move-labware``.

    ``new_location`` is an OT-2 deck slot (``"1"``..``"12"``) or ``"OFF_DECK"``.
    On the OT-2 this records the move (bookkeeping); it does not drive a
    gripper. Home the gantry before an arm/hand enters the deck — the gateway
    will not retract for you (see ``opentrons-server`` ``HTTP_DRIVE_PLAN.md``).
    """

    labware_nickname: str = Field(pattern=ROBOT_REFERENCE_PATTERN)
    new_location: str = Field(..., description='deck slot "1".."12" or "OFF_DECK"')
    use_gripper: bool = False
    pick_up_offset: Optional[GripperOffset] = None
    drop_offset: Optional[GripperOffset] = None

    @model_validator(mode="after")
    def _gripper_options(self) -> "MoveLabwareArgs":
        if not self.use_gripper and (
            self.pick_up_offset is not None or self.drop_offset is not None
        ):
            raise ValueError("gripper offsets require use_gripper=true")
        return self


class PlateLoadArgs(_StrictArgs):
    """Body for ``POST /control/plate/load`` — register the plate the
    orchestrator considers loaded. ``model`` is an Opentrons ``load_name``
    (no gateway default); ``wells`` defaults to 96 empty wells."""

    plate_id: str = Field(..., min_length=1, max_length=128)
    model: str = Field(..., min_length=1)
    wells: Optional[list[Dict[str, Any]]] = None


class WellUpdateArgs(_StrictArgs):
    """Body for ``POST /control/well/update`` — mutate one well of the loaded
    plate (the device-owned ``volume_ul`` plus orchestrator-owned metadata)."""

    well: str = Field(..., min_length=2, max_length=3, description="e.g. A1, H12")
    sample_id: Optional[str] = None
    volume_ul: Optional[float] = Field(default=None, ge=0.0)
    notes: Optional[str] = None
    clear_sample_id: bool = False
    clear_notes: bool = False


class PipetteArgs(_StrictArgs):
    pipette: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class BlowOutArgs(PipetteArgs):
    """Expel residual liquid at a well, or explicitly at the current location."""

    location: Optional[WellLocation] = None
    in_place: bool = False

    @model_validator(mode="after")
    def _one_target(self) -> "BlowOutArgs":
        if (self.location is not None) == self.in_place:
            raise ValueError("provide a location or in_place=true, exclusively")
        return self


class MixArgs(LiquidMoveArgs):
    repetitions: int = Field(ge=1, le=1000)
    rate: float = Field(default=1, gt=0, le=10)

    @model_validator(mode="after")
    def _uses_rate(self) -> "MixArgs":
        if self.flow_rate is not None:
            raise ValueError("mix uses rate; set individual flows with set_flow_rate first")
        return self


class AirGapArgs(PipetteArgs):
    location: WellLocation
    volume_ul: float = Field(gt=0, le=MAX_PIPETTE_VOLUME_UL)
    height: float = Field(default=5, ge=0, le=MAX_WELL_OFFSET_MM)

    @model_validator(mode="after")
    def _height_is_the_only_offset(self) -> "AirGapArgs":
        if (
            self.location.top is not None
            or self.location.bottom is not None
            or self.location.center
        ):
            raise ValueError("air_gap uses height above the well top; omit location offsets")
        return self


class TouchTipArgs(PipetteArgs):
    labware_nickname: str = Field(min_length=1, pattern=ROBOT_REFERENCE_PATTERN)
    position: str = Field(pattern=WELL_NAME_PATTERN)
    radius: float = Field(default=1, gt=0, le=1)
    v_offset: float = Field(default=-1, ge=-MAX_WELL_OFFSET_MM, le=0)
    speed: float = Field(default=60, ge=1, le=80)


class FlowRateArgs(PipetteArgs):
    aspirate: Optional[float] = Field(default=None, gt=0)
    dispense: Optional[float] = Field(default=None, gt=0)
    blow_out: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _at_least_one(self) -> "FlowRateArgs":
        if all(getattr(self, field) is None for field in ("aspirate", "dispense", "blow_out")):
            raise ValueError("provide at least one flow rate in uL/s")
        return self


class PipetteSpeedArgs(PipetteArgs):
    speed: float = Field(gt=0, le=400, description="Explicit gantry move speed in mm/s.")


class ModuleArgs(_StrictArgs):
    module: str = Field(
        min_length=1,
        pattern=ROBOT_REFERENCE_PATTERN,
        description="Loaded module nickname or declared deck slot.",
    )


class HeaterShakerTemperatureArgs(ModuleArgs):
    celsius: float = Field(ge=37, le=95)


class ShakeSpeedArgs(ModuleArgs):
    rpm: int = Field(ge=200, le=3000)


class MagnetEngageArgs(ModuleArgs):
    height_from_base: float = Field(ge=0, le=20, description="Height above labware base in mm.")


class BlockTemperatureArgs(ModuleArgs):
    temperature: float = Field(ge=4, le=99)
    hold_time_seconds: Optional[float] = Field(default=None, ge=0, le=86400)
    block_max_volume: Optional[float] = Field(default=None, gt=0, le=100)


class LidTemperatureArgs(ModuleArgs):
    temperature: float = Field(ge=37, le=110)


class CommentArgs(_StrictArgs):
    message: str = Field(min_length=1, max_length=2000)


class DelayArgs(_StrictArgs):
    seconds: float = Field(ge=0, le=86400)


class GripperMoveAbsoluteArgs(_StrictArgs):
    """Flex gripper mount move; direct axis motion is the reviewed default."""

    force_direct: bool = Field(
        default=True,
        description=(
            "True preserves direct motion without a Z-retract waypoint; false "
            "requests the robot's arced mount move."
        ),
    )
    x: float = Field(ge=0, le=MAX_FLEX_X_MM)
    y: float = Field(ge=0, le=MAX_FLEX_Y_MM)
    z: float = Field(ge=0, le=MAX_Z_MM)
    speed: float = Field(default=50, gt=0, le=400)


class GripperMoveRelativeArgs(_StrictArgs):
    dx: float = Field(default=0, ge=-100, le=100)
    dy: float = Field(default=0, ge=-100, le=100)
    dz: float = Field(default=0, ge=-100, le=100)
    speed: float = Field(default=50, gt=0, le=400)


class TrashBinArgs(_StrictArgs):
    nickname: str = Field(default="default_trash", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    location: str = Field(pattern=r"^[A-D][13]$")


def _http_action(
    name: str,
    endpoint: str,
    args_schema: type[BaseModel],
    description: str,
    *,
    duration: float | None = None,
) -> SkillDef:
    return SkillDef(
        name=name,
        kind="liquid_handler",
        description=description,
        endpoint=endpoint,
        args_schema=args_schema,
        requires_states=["ready"],
        estimated_duration_s=duration,
    )


# Typed union of the model-specific catalogs served by opentrons-server
# e5936de. Runtime discovery decides which subset a particular gateway actually
# implements; the two existing OT-2 registrations are unchanged.
_NEW_HTTP_SKILLS = [
    _http_action("comment", "/control/comment", CommentArgs, "Add a protocol-run comment."),
    _http_action("delay", "/control/delay", DelayArgs, "Delay for 0-86400 seconds."),
    _http_action(
        "blow_out",
        "/control/blow-out",
        BlowOutArgs,
        "Blow out at an explicit loaded well or explicitly in place.",
    ),
    _http_action(
        "touch_tip",
        "/control/touch-tip",
        TouchTipArgs,
        "Touch a loaded well's wall; updates the existing tip-contact tracker.",
    ),
    _http_action(
        "mix", "/control/mix", MixArgs, "Repeat aspirate/dispense mixing in one loaded well."
    ),
    _http_action(
        "air_gap",
        "/control/air-gap",
        AirGapArgs,
        "Aspirate air at a bounded height above an explicit loaded well.",
    ),
    _http_action(
        "prepare_aspirate",
        "/control/prepare-aspirate",
        PipetteArgs,
        "Prepare a named pipette for aspiration.",
    ),
    _http_action("home_pipette", "/control/home-pipette", PipetteArgs, "Home a named pipette."),
    _http_action("home_plunger", "/control/home-plunger", PipetteArgs, "Home a named plunger."),
    _http_action(
        "set_flow_rate",
        "/control/set-flow-rate",
        FlowRateArgs,
        "Set one or more pipette flow rates in microliters per second.",
    ),
    _http_action(
        "set_speed",
        "/control/set-speed",
        PipetteSpeedArgs,
        "Set explicit pipette-move speed in millimeters per second.",
    ),
    _http_action(
        "hs_latch_open", "/control/hs-latch-open", ModuleArgs, "Open a heater-shaker latch."
    ),
    _http_action(
        "hs_latch_close", "/control/hs-latch-close", ModuleArgs, "Close a heater-shaker latch."
    ),
    _http_action(
        "hs_set_and_wait_shake_speed",
        "/control/hs-set-and-wait-shake-speed",
        ShakeSpeedArgs,
        "Set heater-shaker speed to 200-3000 rpm and wait for it.",
    ),
    _http_action(
        "hs_deactivate_shaker",
        "/control/hs-deactivate-shaker",
        ModuleArgs,
        "Stop a heater-shaker's shaker motor.",
    ),
    _http_action(
        "hs_set_target_temperature",
        "/control/hs-set-target-temperature",
        HeaterShakerTemperatureArgs,
        "Set a heater-shaker target from 37-95 °C without waiting.",
    ),
    _http_action(
        "hs_set_and_wait_temperature",
        "/control/hs-set-and-wait-temperature",
        HeaterShakerTemperatureArgs,
        "Set a heater-shaker target from 37-95 °C and wait for it.",
    ),
    _http_action(
        "hs_wait_for_temperature",
        "/control/hs-wait-for-temperature",
        ModuleArgs,
        "Wait for a heater-shaker's existing target temperature.",
    ),
    _http_action(
        "hs_deactivate_heater",
        "/control/hs-deactivate-heater",
        ModuleArgs,
        "Deactivate a heater-shaker heater.",
    ),
    _http_action(
        "hs_deactivate",
        "/control/hs-deactivate",
        ModuleArgs,
        "Deactivate heater-shaker heating and shaking.",
    ),
    _http_action(
        "tempmod_await_temperature",
        "/control/tempmod-await-temperature",
        ModuleArgs,
        "Wait for a temperature module's existing target.",
    ),
    _http_action(
        "magmod_engage",
        "/control/magmod-engage",
        MagnetEngageArgs,
        "Engage an OT-2 magnetic module at an explicit 0-20 mm height from base.",
    ),
    _http_action(
        "magmod_disengage",
        "/control/magmod-disengage",
        ModuleArgs,
        "Disengage an OT-2 magnetic module.",
    ),
    _http_action(
        "thermocycler_open_lid",
        "/control/thermocycler-open-lid",
        ModuleArgs,
        "Open a thermocycler lid.",
    ),
    _http_action(
        "thermocycler_close_lid",
        "/control/thermocycler-close-lid",
        ModuleArgs,
        "Close a thermocycler lid.",
    ),
    _http_action(
        "thermocycler_set_block_temperature",
        "/control/thermocycler-set-block-temperature",
        BlockTemperatureArgs,
        "Set thermocycler block temperature, optional hold, and optional max volume.",
    ),
    _http_action(
        "thermocycler_set_lid_temperature",
        "/control/thermocycler-set-lid-temperature",
        LidTemperatureArgs,
        "Set thermocycler lid temperature from 37-110 °C.",
    ),
    _http_action(
        "thermocycler_deactivate_block",
        "/control/thermocycler-deactivate-block",
        ModuleArgs,
        "Deactivate thermocycler block temperature control.",
    ),
    _http_action(
        "thermocycler_deactivate_lid",
        "/control/thermocycler-deactivate-lid",
        ModuleArgs,
        "Deactivate thermocycler lid temperature control.",
    ),
    _http_action(
        "thermocycler_deactivate",
        "/control/thermocycler-deactivate",
        ModuleArgs,
        "Deactivate thermocycler block and lid temperature control.",
    ),
    _http_action(
        "gripper_move_to_relative",
        "/control/gripper-move-to-relative",
        GripperMoveRelativeArgs,
        "Move a Flex gripper by direct relative axes; dz=0 retains height.",
    ),
    _http_action(
        "gripper_move_to_absolute",
        "/control/gripper-move-to-absolute",
        GripperMoveAbsoluteArgs,
        "Move a Flex gripper to XYZ; direct axis motion is the default.",
    ),
    _http_action(
        "gripper_open_jaw",
        "/control/gripper-open-jaw",
        _NoArgs,
        "Open a Flex gripper jaw.",
    ),
    _http_action(
        "gripper_close_jaw",
        "/control/gripper-close-jaw",
        _NoArgs,
        "Close a Flex gripper jaw with the robot's default force.",
    ),
    _http_action("home_gripper", "/control/home-gripper", _NoArgs, "Home the Flex gripper Z axis."),
    _http_action(
        "load_trash_bin",
        "/control/load-trash-bin",
        TrashBinArgs,
        "Register an explicit Flex movable-trash location in column 1 or 3.",
    ),
]


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
            name="move_to",
            kind="liquid_handler",
            description=(
                "Move a pipette to a well or to absolute deck coordinates "
                "(no liquid handling). Idempotent: safe to re-issue after a "
                "transport loss."
            ),
            endpoint="/control/move-to",
            args_schema=MoveToArgs,
            requires_states=["ready"],
            estimated_duration_s=3.0,
        ),
        SkillDef(
            name="pick_up_tip",
            kind="liquid_handler",
            description=(
                "Pick up a tip. Pass labware_nickname + position for an explicit "
                "tip (required on the HTTP transport). Omit position on a tracked "
                "rack to auto-pick the next available tip (column-major). The "
                "gateway's contamination guard refuses cross-sample reuse "
                "(HTTP 412) unless sample_id matches the prior use or force=True."
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
            description=(
                "Dispense a volume into a well (optional flow_rate and HTTP "
                "push_out in µL). Residual liquid still needs an explicit blow_out."
            ),
            endpoint="/control/dispense",
            args_schema=DispenseArgs,
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
                "Move labware to a deck slot, module/adapter, or OFF_DECK. OT-2 "
                "moves are manual bookkeeping; Flex may use its gripper with "
                "explicit pickup/drop offsets."
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
        SkillDef(
            name="tips.reset",
            kind="liquid_handler",
            description=(
                "(Re)register a tip rack with every tip fresh — marks a physical "
                "rack swap. Racks named in setup register automatically and keep "
                "used-tip statuses across restarts; this is for swapping in a "
                "fresh rack or tracking one loaded out-of-band. Metadata only."
            ),
            endpoint="/control/tips/reset",
            args_schema=TipsResetArgs,
            # Advertised in ready and dry_run (not requires_init/error).
            requires_states=["ready", "dry_run"],
            estimated_duration_s=0.2,
        ),
        SkillDef(
            name="tips.mark",
            kind="liquid_handler",
            description=(
                "Correct part of a tracked tip rack: assert which tips are "
                "actually there. tips.reset is all-or-nothing and would wrongly "
                "claim a full rack; this sets only the named wells or columns. "
                "The repair tool when the tracker has drifted from the bench. "
                "Metadata only — no motion. Proposing is not asserting: the "
                "operator approving the card is who makes the claim."
            ),
            endpoint="/control/tips/mark",
            args_schema=TipsMarkArgs,
            # Metadata only, like tips.reset: advertised in ready and dry_run.
            requires_states=["ready", "dry_run"],
            estimated_duration_s=0.2,
        ),
        # ---- temperature module ------------------------------------------
        # Not dry_run, unlike the metadata verbs above: the gateway withholds
        # tempmod.* in DRY_RUN (the only non-motion verbs it does) and lists
        # both in its _RUN_STARTING_ACTIONS, so it classes them as
        # hardware-driving — same treatment the motion verbs get here.
        SkillDef(
            name="tempmod.set",
            kind="liquid_handler",
            description=(
                "Hold a temperature module at a target between 4 and 95 °C. "
                "The call returns as soon as the module accepts the setpoint — "
                "the ramp runs on afterwards, so read /status (or wait) before "
                "treating the block as at temperature. Name the module by "
                "nickname or deck slot when the deck carries more than one. "
                "This drives hardware: the operator approving the card is who "
                "commits the block to that temperature."
            ),
            endpoint="/control/tempmod/set",
            args_schema=TempmodSetArgs,
            requires_states=["ready"],
            estimated_duration_s=1.0,
        ),
        SkillDef(
            name="tempmod.deactivate",
            kind="liquid_handler",
            description=(
                "Stop holding a temperature: the module powers down its "
                "heating/cooling and the block drifts toward ambient. Returns "
                "immediately; the drift takes as long as it takes. Name the "
                "module by nickname or deck slot when the deck carries more "
                "than one. This drives hardware: the operator approving the "
                "card is who ends temperature control."
            ),
            endpoint="/control/tempmod/deactivate",
            args_schema=TempmodDeactivateArgs,
            requires_states=["ready"],
            estimated_duration_s=1.0,
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
        *_NEW_HTTP_SKILLS,
    ],
)


__all__ = [
    "DeckDeclareArgs",
    "DispenseArgs",
    "GripperMoveAbsoluteArgs",
    "GripperMoveRelativeArgs",
    "GripperOffset",
    "InstrumentSpec",
    "LabwareSpec",
    "LightsSetArgs",
    "LiquidMoveArgs",
    "ModuleSpec",
    "MoveLabwareArgs",
    "MoveToArgs",
    "PlateLoadArgs",
    "SetupArgs",
    "StartupArgs",
    "TempmodDeactivateArgs",
    "TempmodSetArgs",
    "TipArgs",
    "TipsMarkArgs",
    "TipsResetArgs",
    "TrashBinArgs",
    "WellLocation",
    "WellUpdateArgs",
]
