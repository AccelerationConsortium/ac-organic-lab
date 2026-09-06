"""Propose-only control MCP server for the dashboard lab assistant.

This is the ``lab-control`` MCP server from ``docs/UI_DESIGN.md`` §5 (Assistant
control mode, Step 1). It is a *second* console script beside
``lab-history-mcp`` (``app/mcp_server.py``) and is spawned per request by
``app/assistant.py`` only when the operator is in **Control** mode.

Safety
------
**None of the three tools actuate hardware.** The most privileged thing this
server can do is return a *validated proposal object*; the actual control
POST happens later, in the browser, when the operator clicks *Authorize* over
the existing ``/api/equipment/{id}/control/{action}`` passthrough. With no
actuating tool registered, the worst a prompt-injected instruction can
achieve is raising a confirm card a human must read and click — the
read-only-by-construction guarantee of ARCHITECTURE decision #10 survives
(UI_DESIGN §5.1 / §5.4). ``lookup_custom_labware`` (added 2026-08-19) is a
plain read against the dashboard's own labware store (``api/app/labware.py``)
— already served unauthenticated to every dashboard user — so it adds no new
privilege; it exists only to give the model a real definition to attach to a
``deck.declare``/``setup`` proposal instead of a bare, unresolvable
``load_name``.

Actor binding
-------------
The verified operator is passed in the **environment** (``LAB_ACTOR``), never
as a tool argument the model could choose. ``propose_action`` refuses when it
is unset. Per-equipment authorization (``operator``+ on *that* equipment) is
re-checked against the ac_auth sidecar (``AUTH_SERVICE_BASE`` ``/authz/check``)
before a proposal is returned; the check fails closed.

Scope
-----
A proposal is one action on one device (``propose_action``), or — since Step
1i (2026-08-20, operator decision) — one ORDERED sequence of such actions on
one device (``propose_plan``): the same per-step validation, one card, one
approval of the step list's hash, and the browser runs the steps in order,
halting at the first the device refuses. Cross-device work is still out of
scope for both (UI_DESIGN §5.4). In scope:

* ``robot_arm`` move targets (``move.<node_id>`` -> the ``graph.move_to``
  skill -> ``POST /control/graph/move_to``) — the Step 1 surface. Each hop
  is one action. Since Step 1k (2026-09-01, operator decision) multi-hop
  destinations are first-class too: ``travel.<node_id>`` -> the
  ``graph.travel_to`` skill -> ``POST /control/graph/travel_to``, where the
  DEVICE plans and executes the whole whitelisted hop path in one blocking
  call. Startability for travel comes from the ``details.motion_graph``
  snapshot (``reachable_nodes`` + ``travel_targets``) rather than
  ``allowed_actions``, because the device deliberately does not enumerate
  multi-hop targets; the snapshot is also forwarded verbatim for route
  *reasoning* (see :func:`_list_available_actions`). Model-guessed
  ``move.<node_id>`` routes are what this replaces — the model has no edge
  data, so its step-2 hops died on the device's 409 ``edge_not_allowed``.
* the per-kind allowlist in :data:`_PROPOSABLE` — the ``liquid_handler``
  (OT-2) control surface plus, since Step 1d, ``fume_hood`` / ``shaker`` /
  ``press``, since Step 1f, ``camera`` (PTZ nudge, presets, privacy,
  streaming), since Step 1g, the Cytation's finite plate-reader actions,
  since Step 1h, the PlateLoc sealer's finite surface (lifecycle, stage,
  setpoints, ``seal.start``), and since Step 1l (2026-09-02, operator
  request) the solid doser's bounded surface — lid and plate-lift moves,
  lifecycle, tare, plate record, single-well and small-batch dosing;
  ``dose.row`` / ``dose.column`` / ``dose.all`` stay workflow-only as
  unbounded synchronous calls (see the table). The table carries the scope history
  and per-kind rationale; :data:`_FORBIDDEN_ARG_FIELDS` holds the
  argument fields that are never model-settable (interlock overrides,
  device credentials). The ``hplc`` kind is deliberately absent — see
  the table's Step 1d note.

Safety-floor actions are deliberately **not** proposable — they are operator
buttons and must stay reachable without the assistant. That is the xArm's
``stop`` / ``connect`` / ``clear_errors``, and every kind's stop verb
(``sash.stop``, ``shake.stop``, the press's ``stop``, the PlateLoc's
``seal.stop``). Anything the resolver cannot map is refused.

Transport
---------
stdio (what Claude Code expects). Registered by ``assistant.py`` via a
generated ``--mcp-config`` entry; the ``mcp`` package is imported lazily inside
:func:`_build_server` so this module and its tool logic import and unit-test
without it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sys
from typing import Any

import httpx
from pydantic import ValidationError

from lab_skills import Lab, load_registry
from lab_skills.deck_slots import (
    DECK_TOUCHING_SKILLS,
    SlotResolutionError,
    canonicalize_slot_args,
    default_locations,
    find_location,
    location_vocabulary,
    set_default_locations,
    touched_slots,
)
from lab_skills.exceptions import (
    EquipmentInMaintenance,
    EquipmentUnreachable,
    LabError,
)
from lab_skills.registry import EquipmentEntry, Registry
from lab_skills.skill_catalog import SkillDef, skills_for

logger = logging.getLogger(__name__)

# How long the browser should treat a returned proposal as fresh before it
# must be re-proposed. The device's 412/423 at click time remains the real
# backstop; this only bounds how stale the confirm card may be (UI_DESIGN §5.3).
PROPOSAL_TTL_S = 120
# A plan card takes longer to read than a single action's — N steps with
# arguments — and the operator approves it, then runs it, then watches it
# finish; the dashboard keeps the plan record for this long from proposal
# (extended by the same amount on approval) before dropping it.
PLAN_TTL_S = 600
# Review-ability bound. A card nobody can read end to end is a rubber stamp;
# past this the model is told to split the work or recommend a workflow plan.
MAX_PLAN_STEPS = 256

# Every machine code a propose_action / propose_plan refusal can carry (the
# ``_err`` calls in the propose paths). assistant.py matches tool-result
# payloads against this set to emit a visible ``proposal_refused`` frame:
# without one, a refused proposal is indistinguishable from the model never
# proposing at all — the operator sees the request "understood" and no
# authorize button, and the why lives only in prose the model may not write.
REFUSAL_CODES = frozenset(
    {
        "no_actor",
        "unknown_equipment",
        "disabled",
        "unreachable",
        "not_allowed",
        "unmappable_action",
        "invalid_args",
        "forbidden_field",
        "not_authorized",
        "empty_plan",
        "too_many_steps",
        "invalid_step",
        # Step 1m: a slot argument naming another device's place, or a
        # registry place with several keys on this device.
        "wrong_device_location",
        "ambiguous_location",
    }
)

# The reason vocabulary for decline_proposal — mirrors the control prompt's
# "why not" list. An unknown code coerces to "other" rather than erroring:
# the tool exists to TERMINATE a control turn, and must never be one more
# thing that can fail and leave the turn dangling.
DECLINE_REASON_CODES = frozenset(
    {
        "not_proposable",
        "safety_floor",
        "cross_device",
        "too_many_steps",
        "needs_human",
        "device_unavailable",
        "unsafe_state",
        "informational",
        "other",
    }
)


# ---------------------------------------------------------------------------
# Config seams (mirror api/app/control.py so behaviour matches the passthrough)
# ---------------------------------------------------------------------------


def _authz_base() -> str:
    """Auth sidecar base URL (same env the passthrough + middleware use)."""

    return os.environ.get("AUTH_SERVICE_BASE", "http://127.0.0.1:8009")


def _authz_enforced() -> bool:
    """Escape hatch mirroring ``control.py``: set ``CONTROL_AUTHZ_ENFORCE=false``
    for local dev without the auth sidecar. Enforced by default."""

    return os.environ.get("CONTROL_AUTHZ_ENFORCE", "true").lower() != "false"


def _actor() -> str | None:
    """The verified operator bound into this server's environment, or None.

    Set by ``assistant.py`` from the request's ``X-Auth-User`` (injected by the
    Next.js middleware after verifying the session — never client-supplied).
    """

    actor = os.environ.get("LAB_ACTOR", "").strip()
    return actor or None


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class ProposalRefused(Exception):
    """A proposal cannot be formed. Carries a machine code + human message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _err(code: str, message: str, **extra: Any) -> str:
    return _dumps({"error": message, "code": code, **extra})


# ---------------------------------------------------------------------------
# Action-name resolver (the one non-obvious mapping — see module docstring)
# ---------------------------------------------------------------------------


def _find_skill_def(kind: str, name: str) -> SkillDef | None:
    for d in skills_for(kind):
        if d.name == name:
            return d
    return None


def _passthrough_action(sd: SkillDef) -> str:
    """The ``{action}`` segment the browser POSTs to
    ``/api/equipment/{id}/control/{action}`` for this skill's endpoint.

    ``control.py`` composes the device URL as ``<status_path base>/control/`` +
    ``action.lstrip("/")``, so we hand back the endpoint with its ``/control/``
    prefix removed (``/control/graph/move_to`` -> ``graph/move_to``).
    """

    ep = sd.endpoint
    prefix = "/control/"
    if ep.startswith(prefix):
        return ep[len(prefix):]
    return ep.lstrip("/")


def _canonical_action(kind: str | None, action: str) -> str:
    """Map a passthrough URL segment back to the catalog / advertised name.

    ``list_available_actions`` returns both ``action`` (dotted, what the
    device advertises — ``press.up``) and ``passthrough_action`` (the slash
    form the browser POSTs — ``press/up``). Models frequently pass the
    latter to ``propose_action``, which then failed the ``allowed_actions``
    membership check and never emitted a confirm card. Kind-scoped: the
    press's ``init`` maps from ``startup``, which must not collide with the
    shaker's catalog name ``startup``.
    """

    proposable = _PROPOSABLE.get(kind or "", frozenset())
    if action in proposable:
        return action
    for name in proposable:
        sd = _find_skill_def(kind, name)
        if sd is not None and _passthrough_action(sd) == action:
            return name
    return action


# Per-kind allowlist of actions the assistant may propose. Fail-closed: a kind
# absent from this table, or an action absent from its set, is refused — a new
# gateway verb stays operator-only until somebody scopes it here deliberately
# (which is why the table survives even where a kind's whole advertised surface
# is scoped in).
#
# Do not read this table as a claim about *how much* of a device's surface is
# covered; whether it is complete is asserted by tests, not by a comment.
# ``test_liquid_handler_names_match_gateway_allowed_actions`` (skills/tests)
# pins the catalog against the gateway's advertised names, and the
# ``_OT2_SURFACE`` map in api/tests pins this allowlist against the catalog.
# Both fail loudly when a device grows a verb — whereas a completeness claim
# written here just goes quietly stale, which is exactly what happened between
# ``tips.reset`` and the gateway later adding ``tips.mark`` / ``tempmod.*``.
#
# Nothing here actuates and the confirm card is the gate, so the bar is not
# "is this action dangerous". Two things keep that card a real gate rather
# than a rubber stamp, and both are enforced in code, not by this comment:
#
# * **Operator-only argument fields** (:data:`_FORBIDDEN_ARG_FIELDS`). A field
#   that weakens an interlock or carries a credential is never model-settable:
#   ``pick_up_tip.force`` overrides the gateway's cross-contamination guard
#   (AGENTIC_LAB_DESIGN.md §1.2: never weaken an interlock at any layer),
#   ``move_to.force_direct`` opts out of the arced collision-safe path, and
#   ``startup.password`` / ``host_alias`` are the gateway's to supply from its
#   own service env — a model-supplied secret would render on the confirm card
#   and land in the ``assistant_proposal`` audit row. Supplying any of them
#   refuses the whole proposal (code ``forbidden_field``), and
#   list_available_actions strips them from the advertised schema so the model
#   never sees them as settable.
# * **Card evaluability.** Nested argument sets (``setup`` labware lists,
#   ``plate.load`` wells) render on the confirm card as full pretty-printed
#   JSON, never a truncated one-liner — a card nobody can check is a rubber
#   stamp (AssistantBubble's ProposalCard).
#
# Scope history. Step 1b (2026-08-12) admitted tier A (lights.set / home /
# pause) and tier B record edits only, holding the liquid/motion verbs back as
# sequence-bound — one confirm click cannot bind pick-up-tip -> aspirate ->
# dispense -> drop-tip, and the passthrough runs no interlocks to catch a
# half-executed remainder. Step 1c (same day, operator decision) admitted the
# full surface: the operator IS the sequencer, authorizing consecutive cards
# one step at a time, with the field guard shipped first as the price of
# admission. The control-mode prompt instructs the model to propose sequence
# steps strictly in order, one at a time, and to recommend a workflow plan
# once a sequence grows beyond a handful of steps — execute_plan (UI_DESIGN
# §5.5) remains the right surface for real multi-step work.
_PROPOSABLE: dict[str, frozenset[str]] = {
    "liquid_handler": frozenset(
        {
            # Session lifecycle. ``startup`` is only proposable because the
            # guard forbids ``password``/``host_alias`` (the gateway uses its
            # own env); a human authorizing it is the "explicit invocation"
            # the catalog blesses despite ``do_not_call_connect: true``.
            "startup",
            "shutdown",
            # Deck state / convenience.
            "lights.set",
            "home",
            "pause",
            "resume",
            "setup",
            # Liquid handling — sequence-bound; the operator sequences via
            # consecutive confirm cards (Step 1c above).
            "move_to",
            "pick_up_tip",
            "aspirate",
            "dispense",
            "drop_tip",
            "move_labware",
            # Record edits — no motion, but they mutate the lab's *belief*
            # about the deck; a wrong one silently desyncs belief from
            # reality, which is why they still confirm. ``tips.reset``
            # additionally re-arms/disarms the contamination guard's input
            # (a physical rack swap), so its card deserves a careful read;
            # ``tips.mark`` is the partial-rack form of the same edit — the
            # repair for a tracker that has drifted from the bench, which
            # ``tips.reset`` can only fix by over-claiming a full rack.
            "plate.load",
            "plate.unload",
            "well.update",
            "tips.reset",
            "tips.mark",
            "deck.declare",
            # Temperature module — hardware-driving (the gateway withholds
            # both in DRY_RUN and while a run is starting), one scalar arg,
            # range-clamped by the schema.
            "tempmod.set",
            "tempmod.deactivate",
        }
    ),
    # Step 1d (2026-08-12): three more bench kinds, same criterion, no new
    # mechanism. Every admitted action is one card-evaluable act with zero or
    # a few scalar, range-clamped args, and no schema in these kinds carries
    # an interlock-override or credential field, so _FORBIDDEN_ARG_FIELDS
    # gains no entries (the risky-field pinning test covers every kind here
    # automatically). The xArm's safety-floor deviation generalizes into a
    # rule: STOP VERBS ARE NEVER PROPOSABLE ON ANY KIND — ``sash.stop``,
    # ``shake.stop``, and the press's emergency ``stop`` (which forces
    # re-init) stay operator buttons, reachable without the assistant.
    #
    # The HPLC (kind ``hplc``) is deliberately NOT scoped — operator decision,
    # 2026-08-12. Its verbs also fit the criterion poorly: ``run.submit``
    # enqueues an acquisition whose correctness lives in the method/sequence,
    # not on a card; ``workflow.start``/``end`` manage the equipment-blocking
    # campaign lock with role semantics (automation-role claims the
    # assistant's human actor would not hold); ``instrument.standby`` parks
    # the instrument against a FIFO queue the card cannot show.
    "fume_hood": frozenset({"sash.move"}),
    "shaker": frozenset(
        {
            # startup opens a serial port (no credentials — unlike the OT-2's
            # startup, nothing here needs the field guard) and is the routine
            # recovery for the USB-enumeration drops this device has hit.
            "startup",
            "shutdown",
            # One complete cycle: the device owns the duration timer and its
            # watchdog stops the motor, so a lone shake.start is a whole act.
            "shake.start",
            "shake.set_temperature",
            "shake.set_speed",
        }
    ),
    "press": frozenset(
        {
            # init (endpoint /control/startup) restores the known-safe pose:
            # press up, plate out, system ACTIVE.
            "init",
            # The press cycle (plate.in -> press.down -> press.up ->
            # plate.out) is sequence-shaped; Step 1c's discipline applies —
            # the operator is the sequencer, one card per step.
            "press.up",
            "press.down",
            "plate.in",
            "plate.out",
        }
    ),
    # Step 1h (2026-08-19): PlateLoc's complete finite surface except its
    # safety-floor abort. `seal.start` is a bounded cycle (the device caps time
    # at 12 s), and its full temperature/time body fits on the confirm card.
    # Stage motion follows the same one-card/operator-sequenced discipline as
    # the press. Live allowed_actions plus the device's 412 checks enforce
    # heater-stable and stage-in preconditions before a seal can start.
    "plate_sealer": frozenset(
        {
            "startup",
            "shutdown",
            "seal.start",
            "seal.set_temperature",
            "seal.set_time",
            "stage.in",
            "stage.out",
        }
    ),
    # Step 1f (2026-08-18): cameras. Unlike every other proposable kind,
    # ``kind: camera`` is in EQUIP_GUIDE.md's UNGATED_KINDS — PTZ, presets,
    # privacy, and streaming are convenience controls that cannot damage
    # hardware or a sample, the same criterion that keeps them off the
    # CONTROL_PASSWORD lock chip on the tile itself. The full advertised
    # surface minus the un-mappable ``preset/{id}`` delete template (see the
    # module docstring) is admitted with no new mechanism: every action is
    # one card-evaluable act with no interlock-override or credential field,
    # so _FORBIDDEN_ARG_FIELDS gains no entries here either. Names are
    # byte-for-byte what the gateway advertises (slash-separated, unlike
    # every other kind's dotted names — see kasa_tapo_services
    # routes/cameras.py) so the direct name-match branch of _resolve needs
    # no camera-specific bridging.
    "camera": frozenset({"ptz", "preset/save", "preset/goto", "privacy", "streaming"}),
    # Step 1g (2026-08-19): the finite Cytation actions. A read or image is a
    # single bounded request whose complete wells/wavelength/exposure body is
    # visible on the confirm card; drawer and plate-record changes follow the
    # same one-card/operator-sequenced rule already used for the OT-2.
    #
    # `incubator.set_temperature` (2026-08-21) is the same class of act as
    # `shake.set_temperature` / `seal.set_temperature`: one setpoint change
    # whose complete `celsius` body fits on the card. Holding temperature is
    # not an operation in progress on this device. `incubator.stop` stays
    # operator-only (stop verbs are never proposable). Cytation `shake.start`
    # is still absent: unlike the Torrey Pines cycle it has no duration timer
    # and needs a later `shake.stop`, so it is not a standalone act.
    "plate_reader": frozenset(
        {
            "startup",
            "shutdown",
            "drawer.open",
            "drawer.close",
            "plate.load",
            "plate.unload",
            "well.update",
            "read.absorbance",
            "read.fluorescence",
            "read.luminescence",
            "imaging.capture",
            "incubator.set_temperature",
        }
    ),
    # Step 1l (2026-09-02, operator request): the solid doser (dose_every_well,
    # "Dose Every Well"). The complaint was that nothing on this device could
    # be composed in Control mode — the kind was simply absent here, so every
    # advertised action refused as unmappable — and the minimum ask was the
    # plate lift. Admitted with the Step 1d / 1g / 1h criterion and no new
    # resolver mechanism: every action is one card-evaluable act with zero or
    # a few scalar, range-clamped args; no schema carries an interlock-override
    # or credential field (startup's ``config_name`` is a profile name, not a
    # secret), so _FORBIDDEN_ARG_FIELDS gains nothing. This device has NO stop
    # verb — its motion endpoints block until the move completes — so there is
    # no safety-floor row; the loader's own collision guard (it refuses raising
    # the plate under a closed lid) and the device's claim/state checks remain
    # the authority at execution time.
    #
    # Four of these names (lid.open / lid.close / plate.raise / plate.lower)
    # were advertised by the device and driven by the dashboard tile before the
    # catalog carried them; they join skill_catalog/solid_doser.py in the same
    # change (the catalog-parity test would otherwise fail here).
    #
    # Held back as WORKFLOW-ONLY, not as a safety floor: dose.row, dose.column,
    # dose.all. They are unbounded synchronous calls — a well is ~15 s, a row
    # 12 wells, a plate 96 — that no single passthrough request can cover
    # (control.py budgets this kind 120 s, under the Next.js proxy's 130 s
    # cap) and, with no stop verb, nothing could abort them mid-plate.
    # dose.multiple is admitted instead, bounded per step by
    # _ARG_CARDINALITY_LIMITS, so a plan chains small batches the operator
    # watches land one step at a time; whole-line / whole-plate dosing belongs
    # in a validated workflow plan.
    "solid_doser": frozenset(
        {
            "startup",
            "shutdown",
            "home",
            "tare",
            # Plate record + the device's full load/unload sequences
            # (open lid + lower + close / open + raise).
            "plate.set",
            "plate.load",
            "plate.unload",
            # Single-axis loader moves — the Step 1l minimum ask. Measured
            # 2.6–2.9 s each in the audit trail (2026-08-15).
            "lid.open",
            "lid.close",
            "plate.raise",
            "plate.lower",
            # Dosing, bounded: one well, or a small explicit batch.
            "dose.well",
            "dose.multiple",
            # Dispenses for `duration` seconds onto the balance to measure
            # mg/s — a calibration, not a dose into a well.
            "calibrate.flow_rate",
        }
    ),
}

# Argument fields the model may never set, per kind — see the rationale above.
# Enforced in :func:`_resolve` (refusal code ``forbidden_field``) and stripped
# from the schemas :func:`_list_available_actions` advertises. Flat per kind on
# purpose: none of these names has a legitimate model-settable use on any
# action of the kind, and a flat set cannot drift when a schema is reused
# across actions (TipArgs serves both tip verbs).
_FORBIDDEN_ARG_FIELDS: dict[str, frozenset[str]] = {
    "liquid_handler": frozenset({"force", "force_direct", "password", "host_alias"}),
}

# Per-(kind, action) cap on how many entries a collection-valued argument may
# carry in ONE proposal step. Enforced in :func:`_resolve` (refusal code
# ``invalid_args``, message names the split). A request-window bound, not a
# safety rule: the device runs ``dose.multiple`` synchronously at roughly
# ``estimated_duration_s`` (15 s) per well, and the dashboard passthrough holds
# the request open for at most ``control.py``'s per-kind budget (120 s for this
# kind, under the Next.js proxy's 130 s cap). Six wells nominally fit with
# slack for balance settling; a larger job is proposed as consecutive
# ``dose.multiple`` steps of one plan, or recommended as a validated workflow
# plan. ``dose.row`` / ``dose.column`` / ``dose.all`` are not proposable at all
# for the same reason (see :data:`_PROPOSABLE`).
_ARG_CARDINALITY_LIMITS: dict[tuple[str, str], tuple[str, int]] = {
    ("solid_doser", "dose.multiple"): ("well_targets", 6),
}


# ---------------------------------------------------------------------------
# Deck-slot vocabulary (UI_DESIGN §5 Step 1m, 2026-09-04)
# ---------------------------------------------------------------------------
#
# The resolver itself lives in ``lab_skills.deck_slots`` since 2026-09-04 so
# that both writers share it — ``validate_plan`` / ``execute_plan`` on the SDK
# path, and this server on the assistant path (ARCHITECTURE.md decision #1).
# What stays here is the wrapping: the SDK's ``SlotResolutionError`` becomes a
# ``ProposalRefused`` with the same code, and the registry comes from the
# SDK's process-wide default (``locations.yaml``, loaded lazily) unless a host
# installs one.

_get_locations = default_locations
_set_locations = set_default_locations
_find_location = find_location
_DECK_ACTIONS = DECK_TOUCHING_SKILLS
_touched_slots = touched_slots


def _canonicalize_locations(
    entry: EquipmentEntry, action: str, args: dict[str, Any] | None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """:func:`lab_skills.deck_slots.canonicalize_slot_args` with the SDK's
    refusal translated into this server's (same code, same message)."""
    try:
        return canonicalize_slot_args(entry, action, args, _get_locations())
    except SlotResolutionError as exc:
        raise ProposalRefused(exc.code, exc.message) from exc


def _location_vocabulary(entry: EquipmentEntry) -> list[dict[str, Any]]:
    return location_vocabulary(entry, _get_locations())


# ---------------------------------------------------------------------------
# Deck check (operator request, 2026-09-04)
# ---------------------------------------------------------------------------
#
# "The operator needs to be asked to check the OT-2 deck layout before using
# it, or moving anything in or out of it." The prompt now says so, and this
# makes it mechanical: every proposal that touches an OT-2 deck — an OT-2
# verb that names or uses a slot, or an xArm travel to a node the registry
# maps onto an OT-2 slot — carries what the gateway currently believes sits in
# each slot, with the touched slots marked, so the confirm card can print it
# and ask for a physical check. The snapshot is the gateway's belief, never
# the truth: the operator at the bench is the authority on the real deck.

# OT-2 verbs that use or change the deck. Lifecycle, lights, plate/well
# bookkeeping and the temperature module do not touch a slot.
_DECK_ACTIONS = frozenset(
    {
        "setup",
        "deck.declare",
        "move_labware",
        "pick_up_tip",
        "aspirate",
        "dispense",
        "drop_tip",
        "move_to",
        "tips.reset",
        "tips.mark",
        "home",
    }
)


def _slot_sort_key(slot: str) -> tuple[int, str]:
    return (int(slot), "") if slot.isdigit() else (99, slot)


def _deck_slots(status: Any) -> dict[str, dict[str, Any]]:
    """``slot -> {"labware", "id", "tips_available"?}`` from an OT-2 envelope.

    Reads ``details.snapshot.labwares`` — the run engine's own view, the one
    name source the prompt trusts — keyed by slot (the live shape) or as a
    list with ``location.slotName`` (tolerated), plus ``details.tip_racks``
    for the per-rack available-tip count.
    """
    details = status.details if isinstance(status.details, dict) else {}
    snap = details.get("snapshot")
    labwares = snap.get("labwares") if isinstance(snap, dict) else None
    slots: dict[str, dict[str, Any]] = {}
    pairs: list[tuple[Any, Any]] = []
    if isinstance(labwares, dict):
        pairs = list(labwares.items())
    elif isinstance(labwares, list):
        pairs = [
            (((lw.get("location") or {}).get("slotName") if isinstance(lw, dict) else None), lw)
            for lw in labwares
        ]
    for slot, lw in pairs:
        if slot is None or not isinstance(lw, dict):
            continue
        slots[str(slot)] = {
            "labware": lw.get("loadName") or lw.get("load_name"),
            "id": lw.get("id"),
        }
    racks = details.get("tip_racks")
    if isinstance(racks, dict):
        for slot, rack in racks.items():
            if isinstance(rack, dict) and isinstance(rack.get("available"), int):
                slots.setdefault(str(slot), {"labware": None, "id": None})
                slots[str(slot)]["tips_available"] = rack["available"]
    return slots


def _slot_of_nickname(slots: dict[str, dict[str, Any]], nickname: str) -> str | None:
    """The slot holding the labware a nickname-addressed verb names, if the
    snapshot can say (the run engine's id or load_name matches)."""
    for slot, info in slots.items():
        if nickname in (info.get("id"), info.get("labware")):
            return slot
    return None


def _deck_check(status: Any, touched: list[str]) -> dict[str, Any]:
    return {
        "equipment_id": status.equipment_id,
        "touched_slots": sorted(set(touched), key=_slot_sort_key),
        "slots": _deck_slots(status),
    }


def _ot2_deck_check(status: Any, action: str, resolved_args: dict[str, Any], resolved_locations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The deck check for one OT-2 step, or ``None`` when the verb does not
    touch the deck. Touched slots come from the slot arguments and, for
    nickname-addressed verbs, from the snapshot."""
    if action not in _DECK_ACTIONS:
        return None
    touched = _touched_slots(action, resolved_args)
    slots = _deck_slots(status)
    nickname = resolved_args.get("labware_nickname")
    if isinstance(nickname, str):
        slot = _slot_of_nickname(slots, nickname)
        if slot is not None:
            touched.append(slot)
    return _deck_check(status, touched)


async def _arm_target_deck_check(
    registry: Registry, entry: EquipmentEntry, node: str
) -> dict[str, Any] | None:
    """When an arm move targets a node the registry maps onto an OT-2 slot,
    the deck it is about to reach into — read best-effort from that OT-2.
    A read failure is reported, not hidden: the card then says the deck could
    not be read and must be checked by eye."""
    place = _find_location(_get_locations(), node)
    if place is None or not place.equipment or place.equipment == entry.id:
        return None
    target = registry.by_id(place.equipment)
    if target is None or target.kind != "liquid_handler":
        return None
    touched = place.alias_tokens(target.id)
    try:
        status = await _read_status(registry, target.id)
    except (EquipmentInMaintenance, EquipmentUnreachable, LabError) as exc:
        return {
            "equipment_id": target.id,
            "touched_slots": touched,
            "slots": {},
            "unreachable": str(exc),
        }
    return _deck_check(status, touched)


def _merge_deck_checks(checks: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    """One entry per device, touched slots unioned (a plan may reach the same
    deck several times)."""
    merged: dict[str, dict[str, Any]] = {}
    for check in checks:
        if not check:
            continue
        current = merged.get(check["equipment_id"])
        if current is None:
            merged[check["equipment_id"]] = dict(check)
            continue
        touched = set(current["touched_slots"]) | set(check["touched_slots"])
        current["touched_slots"] = sorted(touched, key=_slot_sort_key)
        if not current.get("slots") and check.get("slots"):
            current["slots"] = check["slots"]
            current.pop("unreachable", None)
    return list(merged.values())


def _resolve(entry: EquipmentEntry, action: str, args: dict[str, Any]) -> tuple[SkillDef, str, dict[str, Any]]:
    """Map a device ``allowed_actions`` string to a proposable action.

    Returns ``(skill_def, passthrough_action, resolved_args)`` or raises
    :class:`ProposalRefused`. Scope: ``robot_arm`` move targets and gripper
    states, plus the per-kind allowlist in :data:`_PROPOSABLE`. Anything else
    is refused, as is any proposal supplying an operator-only argument field.
    """

    supplied_forbidden = sorted(
        _FORBIDDEN_ARG_FIELDS.get(entry.kind or "", frozenset()) & set(args or {})
    )
    if supplied_forbidden:
        raise ProposalRefused(
            "forbidden_field",
            f"{', '.join(supplied_forbidden)} is operator-only and never "
            "model-settable (interlock override or device credential); omit it "
            "and re-propose",
        )

    action = _canonical_action(entry.kind, action)

    if entry.kind == "robot_arm" and action.startswith("move."):
        node_id = action[len("move."):]
        if not node_id:
            raise ProposalRefused("unmappable_action", f"malformed move action {action!r}")
        sd = _find_skill_def("robot_arm", "graph.move_to")
        if sd is None:  # pragma: no cover - catalog always registers this
            raise ProposalRefused(
                "unmappable_action", "graph.move_to is not registered in the skill catalog"
            )
        # The node id lives in the action name; merge it into the body the
        # graph.move_to endpoint expects. Model-supplied args (e.g. speed) are
        # kept but node_id from the action name wins.
        resolved = {**(args or {}), "node_id": node_id}
        return sd, _passthrough_action(sd), resolved

    # ``travel.<node_id>`` -> the device's own multi-hop planner (Step 1k,
    # 2026-09-01, operator decision). Same bridging shape as ``move.<node_id>``.
    # The device does not enumerate travel targets in ``allowed_actions`` —
    # startability is gated on the ``details.motion_graph`` snapshot instead
    # (see :func:`_action_startable`) and the device's own path check (409
    # when no whitelisted path exists) is the authority at execution time.
    if entry.kind == "robot_arm" and action.startswith("travel."):
        node_id = action[len("travel."):]
        if not node_id:
            raise ProposalRefused("unmappable_action", f"malformed travel action {action!r}")
        sd = _find_skill_def("robot_arm", "graph.travel_to")
        if sd is None:  # pragma: no cover - catalog always registers this
            raise ProposalRefused(
                "unmappable_action", "graph.travel_to is not registered in the skill catalog"
            )
        resolved = {**(args or {}), "node_id": node_id}
        return sd, _passthrough_action(sd), resolved

    # Same bridging shape as ``move.<node_id>``, for the same reason: the
    # device enumerates one action per *legal* gripper state (whitelisted for
    # its current node and current stroke), so the state travels in the action
    # name and the model cannot name a transition the device would refuse.
    #
    # Step 1e (2026-08-13, operator request): admitted because a gripper change
    # is one card-evaluable act — it is not the pick/place *sequence* that
    # DASHBOARD_ASSISTANT_GRAPH_PLAN.md holds back. A pick is still
    # move -> gripper -> move, three cards the operator sequences, exactly as
    # Step 1c settled for the OT-2's liquid verbs. No new mechanism, no
    # interlock-override or credential field in the schema (so
    # _FORBIDDEN_ARG_FIELDS gains nothing), and the device's own STRICT-mode
    # whitelist remains the authority on what is reachable.
    if entry.kind == "robot_arm" and action.startswith("gripper."):
        state = action[len("gripper."):]
        if not state:
            raise ProposalRefused("unmappable_action", f"malformed gripper action {action!r}")
        sd = _find_skill_def("robot_arm", "graph.gripper")
        if sd is None:  # pragma: no cover - catalog always registers this
            raise ProposalRefused(
                "unmappable_action", "graph.gripper is not registered in the skill catalog"
            )
        resolved = {**(args or {}), "state": state}
        return sd, _passthrough_action(sd), resolved

    if action in _PROPOSABLE.get(entry.kind or "", frozenset()):
        # Direct name lookup: these catalog names are byte-for-byte the strings
        # the device advertises, so no bridging is needed (unlike the xArm's
        # ``move.<node_id>``). ``_passthrough_action`` maps the dotted name's
        # endpoint to its URL segment (``plate.load`` -> ``plate/load``).
        sd = _find_skill_def(entry.kind, action)
        if sd is None:  # pragma: no cover - guarded by the catalog parity test
            raise ProposalRefused(
                "unmappable_action",
                f"{action!r} is allowlisted for kind {entry.kind!r} but is not "
                "registered in the skill catalog",
            )
        limit = _ARG_CARDINALITY_LIMITS.get((entry.kind or "", action))
        if limit is not None:
            field, cap = limit
            value = (args or {}).get(field)
            if isinstance(value, (dict, list, tuple, set)) and len(value) > cap:
                raise ProposalRefused(
                    "invalid_args",
                    f"{action!r} may carry at most {cap} {field} entries per step "
                    f"(got {len(value)}) so one request fits the device's control "
                    f"window; split the work into consecutive {action!r} steps of "
                    "one plan, or recommend a validated workflow plan",
                )
        return sd, _passthrough_action(sd), dict(args or {})

    raise ProposalRefused(
        "unmappable_action",
        f"action {action!r} on kind {entry.kind!r} is not proposable by the assistant "
        "(safety-floor actions and verbs not yet scoped into the allowlist stay "
        "operator-only)",
    )


def _travel_targets(status: Any) -> list[str]:
    """Every node the arm's ``details.motion_graph`` snapshot says is reachable
    right now — single-hop ``reachable_nodes`` plus multi-hop ``travel_targets``.
    Empty when the device publishes no snapshot (no graph loaded, non-arm)."""

    motion_graph = (getattr(status, "details", None) or {}).get("motion_graph")
    if not isinstance(motion_graph, dict):
        return []
    nodes: set[str] = set()
    for key in ("reachable_nodes", "travel_targets"):
        value = motion_graph.get(key)
        if isinstance(value, (list, tuple, set)):
            nodes.update(str(n) for n in value)
    return sorted(nodes)


def _action_startable(entry: EquipmentEntry, status: Any, action: str) -> bool:
    """Can ``action`` start right now, per the device's live signals?

    ``allowed_actions`` is the normal authority (STATUS_SPEC §6.2). The one
    exception is the arm's ``travel.<node_id>`` bridge: the device deliberately
    does not enumerate multi-hop targets as actions, so startability comes from
    the ``details.motion_graph`` snapshot it publishes for exactly this purpose
    — the destination must be in ``reachable_nodes`` or ``travel_targets``. The
    device re-plans and re-checks the route when the call is actually sent."""

    if action in (status.allowed_actions or []):
        return True
    if entry.kind == "robot_arm" and action.startswith("travel."):
        return action[len("travel."):] in _travel_targets(status)
    return False


def _validate_args(sd: SkillDef, resolved_args: dict[str, Any]) -> None:
    try:
        sd.args_schema.model_validate(resolved_args)
    except ValidationError as exc:
        raise ProposalRefused(
            "invalid_args",
            f"args do not validate against the {sd.name!r} schema: {exc.errors()}",
        )


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


async def _check_authz(actor: str, equipment_id: str) -> tuple[bool, str | None]:
    """Does ``actor`` hold ``operator``+ on ``equipment_id``? Fails closed."""

    if not _authz_enforced():
        return True, None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_authz_base()}/authz/check",
                params={"user": actor, "equipment": equipment_id},
            )
            resp.raise_for_status()
            verdict = resp.json()
    except Exception as exc:  # noqa: BLE001 - fail closed on any authz error
        return False, f"authorization check failed: {exc}"
    if not verdict.get("allowed"):
        return False, f"{actor} is not authorized to control {equipment_id}"
    return True, None


# ---------------------------------------------------------------------------
# Tool logic (plain async fns; FastMCP wrappers delegate so this is unit
# testable without the mcp package installed).
# ---------------------------------------------------------------------------


async def _read_status(registry: Registry, equipment_id: str):
    """Live ``/status`` for one device via the SDK (read-only, side-effect-free)."""

    async with Lab.connect(registry=registry) as lab:
        client = lab.get(equipment_id)
        return await client.status()


async def _lookup_custom_labware(load_name: str) -> str:
    """One custom (non-standard) labware's full Opentrons schema-2 definition
    from the dashboard's labware store — the same store the deck-declare
    picker and workflow authors read (``api/app/labware.py``). Attach the
    returned ``definition`` to a ``deck.declare`` / ``setup`` proposal's
    matching field so the gateway derives real geometry instead of guessing
    from ``load_name`` alone; a bare ``load_name`` for a custom labware whose
    name the gateway's ``classify_labware`` regex cannot parse silently
    resolves to ``kind: "unknown"`` with no grid.

    Read-only; the store already serves reads to every dashboard user without
    a session. Covers only custom (repo-committed or uploaded) labware — a
    standard Opentrons definition needs no lookup, just its bare
    ``load_name``.
    """

    from fastapi import HTTPException

    from .labware import _LOCK, _load_all, _require_complete_definition

    with _LOCK:
        merged = _load_all()
    item = merged.get(load_name)
    if item is None:
        return _err(
            "unknown_labware",
            f"no custom labware definition named {load_name!r} in the "
            "dashboard's labware store",
        )
    try:
        definition = _require_complete_definition(
            load_name, item["definition"], item["source"]
        )
    except HTTPException as exc:
        return _err("incomplete_definition", str(exc.detail))
    return _dumps({"load_name": load_name, "source": item["source"], "definition": definition})


async def _list_available_actions(registry: Registry, equipment_id: str) -> str:
    """Live ``allowed_actions`` for a device, each annotated with whether the
    assistant can propose it and (when it can) the JSON-Schema for its args.

    When the device publishes a ``details.motion_graph`` snapshot (today: the
    xArm), it is forwarded verbatim under ``motion_graph`` — read-only path
    context (``current_node``, single-hop ``reachable_nodes``, multi-hop
    ``travel_targets``) so the model can reason about routes instead of seeing
    only the current node's outgoing hops. Since Step 1k (2026-09-01) every
    node in that snapshot also gets a synthesized ``travel.<node_id>`` action
    entry: it bridges to the device's own multi-hop planner
    (``graph.travel_to``), which plans and executes the whole whitelisted hop
    path in one blocking call — the model never has to guess intermediate
    hops (it has no edge data to guess with, which is why guessed
    ``move.<node_id>`` routes died on 409 ``edge_not_allowed``)."""

    entry = registry.by_id(equipment_id)
    if entry is None:
        return _err("unknown_equipment", f"no equipment with id {equipment_id!r}")
    try:
        status = await _read_status(registry, equipment_id)
    except EquipmentInMaintenance:
        return _err("disabled", f"{equipment_id!r} is disabled or under maintenance")
    except (EquipmentUnreachable, LabError) as exc:
        return _err("unreachable", f"could not read /status for {equipment_id!r}: {exc}")

    actions: list[dict[str, Any]] = []
    for action in status.allowed_actions or []:
        info: dict[str, Any] = {"action": action, "proposable": False}
        try:
            sd, passthrough, _ = _resolve(entry, action, {})
        except ProposalRefused:
            actions.append(info)
            continue
        # Operator-only fields are stripped from the schema the model sees —
        # advertising them as settable would invite a proposal the guard must
        # then refuse. They are reported by name so the model can explain the
        # omission if asked.
        schema = sd.args_schema.model_json_schema()
        forbidden = _FORBIDDEN_ARG_FIELDS.get(entry.kind or "", frozenset())
        stripped = sorted(f for f in forbidden if f in schema.get("properties", {}))
        for field in stripped:
            schema["properties"].pop(field)
            if field in schema.get("required", []):
                schema["required"].remove(field)
        info.update(
            proposable=True,
            passthrough_action=passthrough,
            description=sd.description,
            args_schema=schema,
        )
        if stripped:
            info["operator_only_fields"] = stripped
        actions.append(info)

    # Step 1k: the arm's multi-hop travel surface. The device enumerates only
    # single-hop ``move.<node_id>`` actions, so the multi-hop destinations are
    # synthesized here from its motion_graph snapshot — one ``travel.<node_id>``
    # per reachable/travel target, all bridging to ``graph.travel_to``.
    if entry.kind == "robot_arm":
        sd = _find_skill_def("robot_arm", "graph.travel_to")
        listed = {a["action"] for a in actions}
        if sd is not None:
            for node in _travel_targets(status):
                name = f"travel.{node}"
                if name in listed:
                    continue
                actions.append(
                    {
                        "action": name,
                        "proposable": True,
                        "passthrough_action": _passthrough_action(sd),
                        "description": sd.description,
                        "args_schema": sd.args_schema.model_json_schema(),
                        "synthesized_from": "motion_graph",
                    }
                )

    payload: dict[str, Any] = {
        "equipment_id": entry.id,
        "equipment_name": entry.name,
        "kind": entry.kind,
        "equipment_status": status.equipment_status,
        "activity": status.activity,
        "message": status.message,
        "actions": actions,
    }
    motion_graph = (status.details or {}).get("motion_graph")
    if isinstance(motion_graph, dict):
        payload["motion_graph"] = motion_graph
    # Step 1m: the place vocabulary. For an OT-2, each deck slot with its bare
    # key and the names other devices use for the same shelf; for the arm,
    # each registry place it can reach with the node ids that reach it.
    if entry.kind in ("liquid_handler", "robot_arm"):
        vocabulary = _location_vocabulary(entry)
        if vocabulary:
            payload["locations"] = vocabulary
            if entry.kind == "liquid_handler":
                payload["slot_vocabulary"] = (
                    "Deck-slot arguments (tips.reset/tips.mark slot, move_labware "
                    "new_location, setup labware[].location, deck.declare slots keys) "
                    "take the bare key shown under 'slot'. Other spellings of the "
                    "same place (the registry name, 'slot 2', an xArm node id) are "
                    "canonicalised to that key; a place on another device is refused."
                )
    return _dumps(payload)


async def _propose_action(
    registry: Registry,
    equipment_id: str,
    action: str,
    args: dict[str, Any] | None,
    reason: str,
) -> str:
    """Validate a single-equipment action and return a normalized proposal.

    Refuses unless ALL hold: a verified actor is bound; the equipment exists
    and is enabled; ``action`` is in the device's live ``allowed_actions``; the
    resolver maps it to a control endpoint; ``args`` validate against the
    skill schema; and the actor holds ``operator``+ on that equipment.
    """

    actor = _actor()
    if actor is None:
        return _err(
            "no_actor",
            "no verified actor is bound to this session (LAB_ACTOR unset); "
            "control proposals require a signed-in operator",
        )

    entry = registry.by_id(equipment_id)
    if entry is None:
        return _err("unknown_equipment", f"no equipment with id {equipment_id!r}")
    if not entry.enabled or entry.maintenance is not None:
        return _err("disabled", f"{equipment_id!r} is disabled or under maintenance")

    try:
        status = await _read_status(registry, equipment_id)
    except EquipmentInMaintenance:
        return _err("disabled", f"{equipment_id!r} is disabled or under maintenance")
    except (EquipmentUnreachable, LabError) as exc:
        return _err("unreachable", f"could not read /status for {equipment_id!r}: {exc}")

    action = _canonical_action(entry.kind, action)
    if not _action_startable(entry, status, action):
        extra: dict[str, Any] = {"allowed_actions": list(status.allowed_actions or [])}
        message = f"{action!r} is not in {equipment_id!r}'s current allowed_actions"
        if entry.kind == "robot_arm" and action.startswith("travel."):
            targets = _travel_targets(status)
            extra["travel_targets"] = targets
            message = (
                f"{action!r} names a node the arm cannot reach right now; "
                "valid travel destinations are the motion_graph's "
                "reachable_nodes and travel_targets"
            )
        if entry.kind == "robot_arm" and action.startswith(("travel.", "move.")):
            # "ot2_hte/slot_2" (or an OT-2 key) is not a graph node, but the
            # registry knows which nodes reach that shelf — say so instead of
            # leaving the model to guess a node name.
            token = action.split(".", 1)[1]
            place = _find_location(_get_locations(), token.lower())
            if place is not None and place.equipment != entry.id:
                nodes = place.alias_tokens(entry.id)
                if nodes:
                    extra["location_nodes"] = {place.name: nodes}
                    message += (
                        f"; {token!r} is the place {place.name!r} ({place.label}), which "
                        f"this arm reaches via nodes {nodes!r} — propose travel.<node>"
                    )
        return _err("not_allowed", message, **extra)

    try:
        args, resolved_locations = _canonicalize_locations(entry, action, args)
        sd, passthrough, resolved_args = _resolve(entry, action, args)
        _validate_args(sd, resolved_args)
    except ProposalRefused as exc:
        return _err(exc.code, exc.message)

    ok, why = await _check_authz(actor, equipment_id)
    if not ok:
        return _err("not_authorized", why or "not authorized")

    deck_checks: list[dict[str, Any] | None] = []
    if entry.kind == "liquid_handler":
        deck_checks.append(_ot2_deck_check(status, action, resolved_args, resolved_locations))
    elif entry.kind == "robot_arm" and action.startswith(("travel.", "move.")):
        deck_checks.append(
            await _arm_target_deck_check(registry, entry, action.split(".", 1)[1])
        )

    proposal = {
        "equipment_id": entry.id,
        "equipment_name": entry.name,
        "kind": entry.kind,
        "action": action,
        "passthrough_action": passthrough,
        "args": resolved_args,
        "reason": reason,
        "actor": actor,
        "expires_in_s": PROPOSAL_TTL_S,
        "device_state": {
            "equipment_status": status.equipment_status,
            "activity": status.activity,
            "message": status.message,
        },
    }
    if resolved_locations:
        proposal["resolved_locations"] = resolved_locations
    merged_checks = _merge_deck_checks(deck_checks)
    if merged_checks:
        proposal["deck_checks"] = merged_checks
    return _dumps({"proposal": proposal})


def _decline_proposal(reason_code: str, explanation: str) -> str:
    """The no-proposal terminal call (Step 1j, 2026-08-25).

    Control mode's contract is "every reply ends with a terminal lab-control
    call": propose_action / propose_plan when an action is proposed, THIS
    when one is not. Making the decline a tool (rather than prose) is what
    lets the backend enforce the contract mechanically — a control turn that
    ends with no terminal call is detectably incomplete, and the operator
    always sees an explicit on-screen reason instead of inferring one from
    the button's absence.
    """

    code = (reason_code or "").strip()
    if code not in DECLINE_REASON_CODES:
        code = "other"
    text = " ".join((explanation or "").split())
    if not text:
        return _err(
            "empty_explanation",
            "decline_proposal needs a one-line explanation the operator will "
            "read on screen; call it again with one",
        )
    return _dumps({"declined": {"reason_code": code, "explanation": text}})


def plan_step_hash(steps: list[dict[str, Any]]) -> str:
    """Stable digest of a plan's ``(action, args)`` list.

    Canonical JSON (sorted keys, no incidental whitespace) so a re-ordered dict
    or reformatting cannot change it while any change to an action or an
    argument value does. The operator approves THIS value, and the dashboard
    refuses an approval whose hash differs from the plan it cached (409) —
    which is what makes the approval a review of exactly what was shown rather
    than a rubber stamp. Same construction as opentrons-server's
    ``compute_step_hash``, so the two review surfaces agree on what "the same
    plan" means.
    """

    payload = json.dumps(
        [{"action": s["action"], "args": s.get("args") or {}} for s in steps],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _propose_plan(
    registry: Registry,
    equipment_id: str,
    steps: list[dict[str, Any]] | None,
    reason: str,
) -> str:
    """Validate an ordered multi-step sequence on ONE device and return a
    normalized plan proposal (Step 1i).

    Every gate :func:`_propose_action` applies, applied per step: a verified
    actor is bound; the equipment exists, is enabled and reachable; each step
    resolves to a proposable action with schema-valid args and no operator-only
    field; the actor holds ``operator``+ on the equipment. The one deliberate
    difference is *which* steps are held to the device's live
    ``allowed_actions``: only the first. Later steps are legal only once the
    earlier ones have run (``seal.start`` after ``stage.in``, ``aspirate``
    after ``pick_up_tip``, the xArm's next hop after this one), so the live
    list cannot vouch for them at proposal time. The device re-checks every
    step when it is actually sent and refuses (412/423) anything its state
    does not allow; the browser halts the plan at the first refusal and marks
    the rest skipped — never continue-past-error.
    """

    actor = _actor()
    if actor is None:
        return _err(
            "no_actor",
            "no verified actor is bound to this session (LAB_ACTOR unset); "
            "control proposals require a signed-in operator",
        )
    if not isinstance(steps, list) or not steps:
        return _err("empty_plan", "a plan needs at least one step ({action, args})")
    if len(steps) > MAX_PLAN_STEPS:
        return _err(
            "too_many_steps",
            f"a plan may carry at most {MAX_PLAN_STEPS} steps (got {len(steps)}); "
            "split the work, or recommend a validated workflow plan",
        )

    entry = registry.by_id(equipment_id)
    if entry is None:
        return _err("unknown_equipment", f"no equipment with id {equipment_id!r}")
    if not entry.enabled or entry.maintenance is not None:
        return _err("disabled", f"{equipment_id!r} is disabled or under maintenance")

    try:
        status = await _read_status(registry, equipment_id)
    except EquipmentInMaintenance:
        return _err("disabled", f"{equipment_id!r} is disabled or under maintenance")
    except (EquipmentUnreachable, LabError) as exc:
        return _err("unreachable", f"could not read /status for {equipment_id!r}: {exc}")

    resolved_steps: list[dict[str, Any]] = []
    # Step-tagged place labels for the card. Kept OUTSIDE ``steps`` so the
    # step hash the operator approves covers exactly what the browser sends.
    plan_locations: list[dict[str, Any]] = []
    deck_checks: list[dict[str, Any] | None] = []
    for index, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict) or not isinstance(raw.get("action"), str):
            return _err(
                "invalid_step",
                f"step {index} must be an object with a string 'action' and optional 'args'",
                step=index,
            )
        args = raw.get("args") or {}
        if not isinstance(args, dict):
            return _err("invalid_step", f"step {index}: args must be an object", step=index)
        action = _canonical_action(entry.kind, raw["action"])
        if index == 1 and not _action_startable(entry, status, action):
            return _err(
                "not_allowed",
                f"step 1 {action!r} is not in {equipment_id!r}'s current "
                "allowed_actions, so the plan cannot start",
                step=1,
                allowed_actions=list(status.allowed_actions or []),
            )
        try:
            args, step_locations = _canonicalize_locations(entry, action, args)
            sd, passthrough, resolved_args = _resolve(entry, action, args)
            _validate_args(sd, resolved_args)
        except ProposalRefused as exc:
            return _err(exc.code, f"step {index} ({action}): {exc.message}", step=index)
        plan_locations.extend({"step": index, **item} for item in step_locations)
        if entry.kind == "liquid_handler":
            deck_checks.append(_ot2_deck_check(status, action, resolved_args, step_locations))
        elif entry.kind == "robot_arm" and action.startswith(("travel.", "move.")):
            deck_checks.append(
                await _arm_target_deck_check(registry, entry, action.split(".", 1)[1])
            )
        resolved_steps.append(
            {"action": action, "passthrough_action": passthrough, "args": resolved_args}
        )

    ok, why = await _check_authz(actor, equipment_id)
    if not ok:
        return _err("not_authorized", why or "not authorized")

    plan = {
        "plan_id": secrets.token_urlsafe(9),
        "equipment_id": entry.id,
        "equipment_name": entry.name,
        "kind": entry.kind,
        "steps": resolved_steps,
        "step_hash": plan_step_hash(resolved_steps),
        "reason": reason,
        "actor": actor,
        "expires_in_s": PLAN_TTL_S,
        "device_state": {
            "equipment_status": status.equipment_status,
            "activity": status.activity,
            "message": status.message,
        },
    }
    if plan_locations:
        plan["resolved_locations"] = plan_locations
    merged_checks = _merge_deck_checks(deck_checks)
    if merged_checks:
        plan["deck_checks"] = merged_checks
    return _dumps({"plan": plan})


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------


def _build_server(registry: Registry):
    """Build the FastMCP server. ``mcp`` is imported here so the module and its
    tool logic import without the package installed (matches mcp_server.py)."""

    from mcp.server.fastmcp import FastMCP

    server = FastMCP("lab-control")

    @server.tool()
    async def list_available_actions(equipment_id: str) -> str:
        """The device's live ``allowed_actions`` plus, for each action the
        assistant can propose, its argument JSON-Schema. Call this before
        propose_action to learn what is legal instead of guessing endpoints.
        Safety-floor actions stay operator-only (``proposable: false``), and
        each proposable action's schema omits its operator-only argument
        fields (listed under ``operator_only_fields``) — never supply those.
        Graph-constrained arms also return a read-only ``motion_graph``
        snapshot (current_node, single-hop reachable_nodes, multi-hop
        travel_targets) plus one synthesized ``travel.<node_id>`` action per
        target in it: the device plans and runs the whole multi-hop route
        itself. To move the arm anywhere, propose ``travel.<node_id>`` for
        the destination — never guess intermediate ``move.<node_id>`` hops
        (the graph's edges are not visible to you, so a guessed route will
        be refused by the device). OT-2s and the arm also return
        ``locations`` — the deck-slot vocabulary: for an OT-2 each slot's
        bare key (the only form its arguments take) with the names other
        devices use for the same shelf; for the arm each shelf it can reach
        with the node ids that reach it. Read it before naming a slot."""

        return await _list_available_actions(registry, equipment_id)

    @server.tool()
    async def lookup_custom_labware(load_name: str) -> str:
        """Fetch one custom labware's full Opentrons schema-2 definition from
        the dashboard's labware store. Call this BEFORE proposing
        deck.declare or setup for any load_name that is not a standard
        built-in Opentrons definition, then include the returned
        "definition" object in the proposal's matching field (deck.declare's
        per-slot {"load_name":..., "definition":...}, or setup's labware[].
        config for a non-ot_default entry). A bare load_name for custom
        labware resolves to unusable geometry (kind "unknown", no grid) on
        the gateway. Returns an error object if load_name is not in the
        store — do not guess or fabricate a definition in that case; tell
        the operator it needs to be uploaded first."""

        return await _lookup_custom_labware(load_name)

    @server.tool()
    async def propose_action(
        equipment_id: str,
        action: str,
        args: dict | None = None,
        reason: str = "",
    ) -> str:
        """Propose ONE action on ONE device for the operator to authorize. Does
        NOT actuate hardware — it returns a validated proposal that the operator
        confirms with a click. ``action`` must be one from
        list_available_actions; ``reason`` is a one-line justification shown
        subordinate to the device's authoritative state. Returns an ``error`` +
        ``code`` object when the proposal is refused."""

        return await _propose_action(registry, equipment_id, action, args, reason)

    @server.tool()
    async def propose_plan(
        equipment_id: str,
        steps: list[dict[str, Any]],
        reason: str = "",
    ) -> str:
        """Propose an ORDERED multi-step sequence on ONE device that the
        operator approves and runs as a whole. Use this instead of several
        propose_action calls whenever the user wants more than one step on
        the same device (stage.in -> seal.start -> stage.out; pick_up_tip ->
        aspirate -> dispense -> drop_tip; the xArm's travel.<pick> ->
        gripper.<grip> -> travel.<place>). Does NOT actuate hardware: it
        returns a validated plan that renders as one card; the operator
        approves the step list as shown and then runs it, and the browser
        sends the steps in order, stopping at the first one the device
        refuses. ``steps`` is a list of
        {"action": <name from list_available_actions>, "args": {...}} in
        execution order. Later steps may depend on earlier ones — only the
        first step must be in the device's current allowed_actions. One
        device per plan, at most 256 steps; safety-floor actions
        (stop verbs, the xArm's connect/clear_errors) are never proposable.
        Returns an ``error`` + ``code`` object (with the failing ``step``
        number) when refused."""

        return await _propose_plan(registry, equipment_id, steps, reason)

    @server.tool()
    async def decline_proposal(reason_code: str, explanation: str = "") -> str:
        """End a control-mode reply WITHOUT proposing. Call this whenever the
        user's request will not get a propose_action/propose_plan call this
        turn: the action is out of scope or safety-floor, spans devices,
        exceeds the step cap, the device is unavailable, the state is unsafe
        — or the exchange was purely informational and there is simply no
        action to propose (reason_code "informational"). ``reason_code`` is
        one of: not_proposable, safety_floor, cross_device, too_many_steps,
        needs_human, device_unavailable, unsafe_state, informational, other.
        ``explanation`` is ONE line the operator reads on screen. Every
        control-mode reply must end with exactly one terminal call —
        propose_action, propose_plan, or this."""

        return _decline_proposal(reason_code, explanation)

    return server


def run() -> None:
    """Entry point for the ``lab-control-mcp`` console script (pyproject).

    Boots the FastMCP server over stdio and blocks until the client closes the
    stream. Logs go to stderr; stdout is reserved for MCP JSON-RPC framing.
    """

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    registry = load_registry()
    locations = _get_locations()
    logger.info(
        "lab-control MCP server: %d devices, %d locations, actor=%s, authz_enforced=%s",
        len(registry.equipment),
        len(locations.locations),
        _actor(),
        _authz_enforced(),
    )
    _build_server(registry).run()


if __name__ == "__main__":  # pragma: no cover
    run()


__all__ = [
    "run",
    "PROPOSAL_TTL_S",
    "PLAN_TTL_S",
    "MAX_PLAN_STEPS",
    "REFUSAL_CODES",
    "DECLINE_REASON_CODES",
    "plan_step_hash",
]
