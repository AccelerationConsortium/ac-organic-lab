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
  is one action; a route is a plan of hops. For route *reasoning*,
  ``list_available_actions`` forwards the device's read-only
  ``details.motion_graph`` snapshot (see :func:`_list_available_actions`).
* the per-kind allowlist in :data:`_PROPOSABLE` — the ``liquid_handler``
  (OT-2) control surface plus, since Step 1d, ``fume_hood`` / ``shaker`` /
  ``press``, since Step 1f, ``camera`` (PTZ nudge, presets, privacy,
  streaming), since Step 1g, the Cytation's finite plate-reader actions,
  and since Step 1h, the PlateLoc sealer's finite surface (lifecycle,
  stage, setpoints, ``seal.start``). The table carries the scope history
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
MAX_PLAN_STEPS = 40


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
# (which is why the table survives even now that it lists the OT-2's whole
# advertised surface).
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
            # (a physical rack swap), so its card deserves a careful read.
            "plate.load",
            "plate.unload",
            "well.update",
            "tips.reset",
            "deck.declare",
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
    # The incubator and shaker are deliberately absent despite being in the
    # catalog. `incubator.set_temperature` and `shake.start` outlive the POST
    # and need a later stop, so neither is a correct standalone act. Their stop
    # verbs remain part of the operator safety floor. Use an authorized
    # workflow that contains the full start/use/stop sequence instead.
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
        return sd, _passthrough_action(sd), dict(args or {})

    raise ProposalRefused(
        "unmappable_action",
        f"action {action!r} on kind {entry.kind!r} is not proposable by the assistant "
        "(safety-floor actions and verbs not yet scoped into the allowlist stay "
        "operator-only)",
    )


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
    only the current node's outgoing hops. This widens what the model can
    *see*, never what it can *propose*: multi-hop travel is not a single
    action. A route is a plan of ``move.<node_id>`` hops (``propose_plan``),
    each hop whitelisted live by the device as it is sent."""

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
    if action not in (status.allowed_actions or []):
        return _err(
            "not_allowed",
            f"{action!r} is not in {equipment_id!r}'s current allowed_actions",
            allowed_actions=list(status.allowed_actions or []),
        )

    try:
        sd, passthrough, resolved_args = _resolve(entry, action, args or {})
        _validate_args(sd, resolved_args)
    except ProposalRefused as exc:
        return _err(exc.code, exc.message)

    ok, why = await _check_authz(actor, equipment_id)
    if not ok:
        return _err("not_authorized", why or "not authorized")

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
    return _dumps({"proposal": proposal})


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
        if index == 1 and action not in (status.allowed_actions or []):
            return _err(
                "not_allowed",
                f"step 1 {action!r} is not in {equipment_id!r}'s current "
                "allowed_actions, so the plan cannot start",
                step=1,
                allowed_actions=list(status.allowed_actions or []),
            )
        try:
            sd, passthrough, resolved_args = _resolve(entry, action, args)
            _validate_args(sd, resolved_args)
        except ProposalRefused as exc:
            return _err(exc.code, f"step {index} ({action}): {exc.message}", step=index)
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
        travel_targets) for planning and explaining routes; propose a route
        as one plan of ``move.<node_id>`` hops (propose_plan)."""

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
        aspirate -> dispense -> drop_tip; a route of move.<node_id> hops).
        Does NOT actuate hardware: it returns a validated plan that renders
        as one card; the operator approves the step list as shown and then
        runs it, and the browser sends the steps in order, stopping at the
        first one the device refuses. ``steps`` is a list of
        {"action": <name from list_available_actions>, "args": {...}} in
        execution order. Later steps may depend on earlier ones — only the
        first step must be in the device's current allowed_actions. One
        device per plan, at most MAX_PLAN_STEPS steps; safety-floor actions
        (stop verbs, the xArm's connect/clear_errors) are never proposable.
        Returns an ``error`` + ``code`` object (with the failing ``step``
        number) when refused."""

        return await _propose_plan(registry, equipment_id, steps, reason)

    return server


def run() -> None:
    """Entry point for the ``lab-control-mcp`` console script (pyproject).

    Boots the FastMCP server over stdio and blocks until the client closes the
    stream. Logs go to stderr; stdout is reserved for MCP JSON-RPC framing.
    """

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    registry = load_registry()
    logger.info(
        "lab-control MCP server: %d devices, actor=%s, authz_enforced=%s",
        len(registry.equipment),
        _actor(),
        _authz_enforced(),
    )
    _build_server(registry).run()


if __name__ == "__main__":  # pragma: no cover
    run()


__all__ = ["run", "PROPOSAL_TTL_S", "PLAN_TTL_S", "MAX_PLAN_STEPS", "plan_step_hash"]
