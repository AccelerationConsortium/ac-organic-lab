"""Lab assistant chat endpoint (Claude Code subprocess backend).

The browser bubble (``web/src/components/AssistantBubble.tsx``) POSTs to
``/api/assistant/chat`` and consumes Server-Sent Events. Instead of calling
the Anthropic API directly (which would need ``ANTHROPIC_API_KEY``), this
endpoint shells out to the locally-installed ``claude`` CLI in
non-interactive mode. That subprocess uses the dashboard user's Claude
Code OAuth login and automatically inherits the ``lab-history`` MCP server
that was registered with ``claude mcp add``, so the same eight read-only
tools are available without any API plumbing.

Configuration
-------------
* ``ASSISTANT_CLAUDE_BIN`` -- override the binary path (default: first
  ``claude`` on PATH).
* ``ASSISTANT_CLAUDE_MODEL`` -- model alias passed to ``claude --model``;
  default ``sonnet`` to keep cost off the Opus tier.
* ``ASSISTANT_CLAUDE_CONTROL_MODEL`` -- model for Control-mode turns only
  (default: same as ``ASSISTANT_CLAUDE_MODEL``). Lets a deployment run Ask
  mode on a faster model (e.g. ``haiku``) without dropping proposal turns.
* ``ASSISTANT_BACKEND`` / ``ASSISTANT_CONTROL_BACKEND`` -- which engine
  answers each mode: ``claude-cli`` (default; this module's subprocess) or
  ``openai`` (``assistant_openai.py``: any OpenAI-compatible endpoint, e.g.
  OpenRouter — see that module for its ``ASSISTANT_OPENAI_*`` config).
* ``ASSISTANT_CLAUDE_CWD`` -- working directory for the subprocess. Defaults
  to a minimal runtime dir *outside* the repo tree (``_runtime_dir()``) so
  Claude Code does not auto-load the repo's large ``CLAUDE.md`` and its
  ~50k-token doc imports on every turn -- that context was recreating the
  prompt cache each request and burning the account's usage limit. The
  ``lab-history`` MCP server no longer depends on cwd: it is passed
  explicitly via ``--mcp-config`` (see below).
* ``ASSISTANT_RUNTIME_DIR`` -- override the runtime dir that holds the
  generated ``mcp.json`` and serves as the default cwd
  (default ``~/.cache/lab-assistant``).
* ``ASSISTANT_CLAUDE_TIMEOUT_S`` -- hard wallclock cap per turn
  (default 120).

Safety
------
* ``--allowedTools mcp__lab-history__*`` restricts the subprocess to the
  lab MCP server. Bash, file ops, web search, etc. are not in the allowlist
  and will be denied if the model tries to call them.
* ``--mcp-config <mcp.json> --strict-mcp-config`` injects *only* the
  read-only ``lab-history`` server, ignoring any filesystem-discovered MCP
  config. This decouples tool availability from cwd, so the minimal cwd
  above keeps working.
* ``--permission-mode default`` keeps Claude Code's normal permission
  prompts; with the empty allowlist for everything else, the model can't
  silently use forbidden tools.
* ``--no-session-persistence`` so each request is a fresh agent loop --
  the conversation history is passed in the prompt, not via session resume.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .assistant_control import PLAN_TTL_S, REFUSAL_CODES, plan_step_hash

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_MODEL = os.environ.get("ASSISTANT_CLAUDE_MODEL", "sonnet")
# Control mode may pin a different (typically stronger) model than Ask mode:
# proposals involve sequence discipline and motion-graph routing, while Ask
# answers are terse status lookups a faster model handles fine. Defaults to
# the same model, so deployments opt into the split explicitly.
CONTROL_MODEL = os.environ.get("ASSISTANT_CLAUDE_CONTROL_MODEL", DEFAULT_MODEL)
DEFAULT_TIMEOUT_S = float(os.environ.get("ASSISTANT_CLAUDE_TIMEOUT_S", "120"))
# Backend per mode: "claude-cli" (this module's subprocess, OAuth-billed) or
# "openai" (assistant_openai.py — an OpenAI-compatible endpoint such as
# OpenRouter, API-key-billed). Both drive the same MCP servers and emit the
# same SSE frames; the bubble cannot tell them apart.
DEFAULT_BACKEND = os.environ.get("ASSISTANT_BACKEND", "claude-cli")
CONTROL_BACKEND = os.environ.get("ASSISTANT_CONTROL_BACKEND", DEFAULT_BACKEND)
ALLOWED_TOOL_GLOB = "mcp__lab-history__*"
# Server-side include-list for the spawned lab-history server (its
# LAB_HISTORY_TOOLS env var). The assistant deliberately does not get the
# dosing-run data tools (query_runs, query_well_results): run/well outcomes
# are experiment data, not platform telemetry, and everything a tool can
# return transits the model provider (OpenRouter on the openai backend).
# Server-side because only the claude CLI has a client-side filter — the
# openai backend registers whatever list_tools returns. Fail-closed: a tool
# added to the server later stays invisible here until named. Override per
# deployment via ASSISTANT_HISTORY_TOOLS.
HISTORY_TOOLS = os.environ.get(
    "ASSISTANT_HISTORY_TOOLS",
    "list_equipment_now,get_equipment_status,record_observation,"
    "query_equipment_events,query_service_uptime,query_sensor_readings,"
    "tail_journald",
)
# Chemical stock (bitácora's /inventory API, read-only) rides its own server
# in both modes; see app/inventory_mcp.py for the contract-stability note.
INVENTORY_TOOL_GLOB = "mcp__lab-inventory__*"
# Control mode (UI_DESIGN §5) adds the propose-only lab-control server. Neither
# of its tools actuates; the model's most privileged act is returning a
# validated proposal the operator then authorizes in the browser.
CONTROL_TOOL_GLOB = "mcp__lab-control__*"


def _repo_root() -> Path:
    # api/app/assistant.py -> api/app -> api -> repo root
    return Path(__file__).resolve().parents[2]


def _claude_binary() -> str | None:
    """Resolve the path to the Claude Code CLI.

    The dashboard runs under systemd with a minimal PATH that excludes
    ``~/.local/bin``, so ``shutil.which`` alone usually returns None on the
    lab host even when the user has it installed. We honour an explicit
    override, then fall back to a small list of well-known install paths
    so a default install just works without editing the unit.
    """

    override = os.environ.get("ASSISTANT_CLAUDE_BIN")
    if override:
        return override
    found = shutil.which("claude")
    if found:
        return found
    candidates = [
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/opt/claude/bin/claude"),
        # Common second home when the service user differs from the
        # interactive user that ran `claude login`.
        Path("/home/sdl2/.local/bin/claude"),
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def _runtime_dir() -> Path:
    """Minimal scratch dir for the subprocess: holds the generated
    ``mcp.json`` and doubles as the default cwd. Deliberately outside the
    repo tree so Claude Code finds no project ``CLAUDE.md`` to load."""

    d = Path(
        os.environ.get(
            "ASSISTANT_RUNTIME_DIR", str(Path.home() / ".cache" / "lab-assistant")
        )
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


def _uv_binary() -> str:
    """Resolve ``uv`` for the MCP server spawn, mirroring _claude_binary()'s
    PATH caveat under systemd."""

    found = shutil.which("uv")
    if found:
        return found
    for c in (Path.home() / ".local" / "bin" / "uv", Path("/usr/local/bin/uv")):
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return "uv"


def _mcp_server_command(script: str) -> tuple[str, list[str]]:
    """Resolve how to launch one of our MCP servers: ``(command, args)``.

    Prefer the console script installed beside the *running* interpreter — i.e.
    the same venv serving this app. Going through ``uv run --project`` instead
    makes every chat turn depend on uv being able to sync the project, and uv
    needs ``~/.cache/uv`` **writable**. The deployed unit sets
    ``ProtectHome=read-only``, so under systemd `uv run` fails and the CLI
    reports the server as ``status: "failed"`` — with no tools, no error frame,
    and nothing in the journal. The model then says its tools are unreachable,
    which reads like a connectivity problem rather than a sandbox one.

    Launching the console script directly needs no writable HOME at all
    (verified against a read-only-home sandbox), so it survives the hardening.
    The ``uv run`` path is kept as a fallback for a dev checkout where the api
    package is not installed into the interpreter's own environment.
    """

    candidates = [Path(sys.executable).parent / script]
    found = shutil.which(script)
    if found:
        candidates.append(Path(found))
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c), []
    return _uv_binary(), ["run", "--project", str(_repo_root() / "api"), script]


def _control_server_env(actor: str) -> dict[str, str]:
    """Environment for the spawned ``lab-control`` MCP server.

    The verified actor is bound here (``LAB_ACTOR``) rather than passed as a
    tool argument, so the model cannot choose whose authority it borrows
    (UI_DESIGN §5.3). Selected config vars are forwarded so the control server
    resolves the same registry + authz sidecar as the dashboard.
    """

    env = {"LAB_ACTOR": actor}
    for key in (
        "AUTH_SERVICE_BASE",
        "CONTROL_AUTHZ_ENFORCE",
        "LAB_REGISTRY_PATH",
        "LAB_DASHBOARD_API_URL",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _history_server_env(actor: str | None) -> dict[str, str]:
    """Environment for the spawned ``lab-history`` MCP server.

    The verified actor rides the environment for the same reason as
    lab-control's ``LAB_ACTOR`` (UI_DESIGN §5.3): record_observation stamps its
    journal rows with it and fails closed when it is absent. Everything else
    on lab-history ignores it. ``LAB_HISTORY_TOOLS`` narrows the server's
    registered toolset to :data:`HISTORY_TOOLS` — the server-side twin of the
    CLI's ``--allowedTools``, and the only filter the openai backend has.
    Shared by both backends (``_write_mcp_config`` here and
    ``assistant_openai._server_specs``) so the toolsets cannot drift.
    """

    env: dict[str, str] = {"LAB_HISTORY_TOOLS": HISTORY_TOOLS}
    if actor:
        env["LAB_ACTOR"] = actor
    dashboard_url = os.environ.get("LAB_DASHBOARD_API_URL")
    if dashboard_url:
        env["LAB_DASHBOARD_API_URL"] = dashboard_url
    return env


def _write_mcp_config(*, include_control: bool = False, actor: str | None = None) -> Path:
    """Materialise the explicit MCP config and return its path.

    Always registers the read-only ``lab-history`` and ``lab-inventory``
    servers. When ``include_control`` (Control mode with a verified ``actor``),
    also registers the propose-only ``lab-control`` server. Every path written here is
    absolute (see :func:`_mcp_server_command`), so the servers resolve
    regardless of the subprocess cwd.
    """

    history_cmd, history_args = _mcp_server_command("lab-history-mcp")
    history_env = _history_server_env(actor)
    inventory_cmd, inventory_args = _mcp_server_command("lab-inventory-mcp")
    inventory_env: dict[str, str] = {}
    bitacora_url = os.environ.get("BITACORA_URL")
    if bitacora_url:
        inventory_env["BITACORA_URL"] = bitacora_url
    servers: dict[str, Any] = {
        "lab-history": {
            "type": "stdio",
            "command": history_cmd,
            "args": history_args,
            "env": history_env,
        },
        "lab-inventory": {
            "type": "stdio",
            "command": inventory_cmd,
            "args": inventory_args,
            "env": inventory_env,
        },
    }
    if include_control and actor:
        control_cmd, control_args = _mcp_server_command("lab-control-mcp")
        servers["lab-control"] = {
            "type": "stdio",
            "command": control_cmd,
            "args": control_args,
            "env": _control_server_env(actor),
        }
    # A distinct filename per mode so a control-mode config never lingers into
    # a later ask-mode turn (and vice versa).
    name = "mcp.control.json" if include_control and actor else "mcp.json"
    path = _runtime_dir() / name
    path.write_text(json.dumps({"mcpServers": servers}, indent=2))
    return path


def _claude_cwd() -> str:
    override = os.environ.get("ASSISTANT_CLAUDE_CWD")
    if override:
        return override
    return str(_runtime_dir())


SYSTEM_PROMPT = """You are the AC Organic Self-driving Lab assistant. You help
lab operators understand what is happening to equipment in real time and
across history.

You have two MCP servers connected: lab-history (equipment telemetry) and
lab-inventory (chemical stock). Their tools read the lab; none of them can
actuate hardware.

lab-history:

* list_equipment_now -- live snapshot of every device (id, kind, equipment_status,
  message, fetch_error, latency_ms). Use this first when you need the canonical
  equipment_id for other tools, or to answer "what's running right now".
* get_equipment_status -- the full live envelope for ONE device: components
  (e.g. the OT-2's pipette mounts), details (deck snapshot, tip racks, loaded
  plate), metrics, allowed_actions, activity. Use it whenever the question is
  about a device's hardware, subsystems, or what it is equipped with —
  list_equipment_now alone cannot answer those.
* query_equipment_events -- past state transitions, errors, startup/shutdown
  for one device.
* query_service_uptime -- reachability transitions + overall uptime % over a
  window for one device.
* query_sensor_readings -- environmental sensor history (~1/min).
* tail_journald -- last N lines of one of the dashboard's systemd units.
* record_observation -- append ONE operational note about a device to the
  shared journal (it comes back to future sessions via
  query_equipment_events(event_type="agent_observation")). Journal only
  platform knowledge -- device behavior, recurring faults, quirks, recovery
  steps that worked -- and only when the user asks you to note something or
  you verified a finding worth keeping. NEVER journal scientific/project
  content (compounds, designs, results) or routine conversation; notes are
  permanent and visible to the whole lab. Before an investigation, check the
  journal for prior notes on that device.

lab-inventory (what is on the shelf — chemicals and bottles, not devices):

* search_inventory -- find chemicals by name, CAS, or synonym; each match
  includes per-bottle group, location, amount remaining, and unit. Empty
  query browses.
* check_stock -- "is there enough X": sufficiency for one CAS, optionally
  against a needed amount + unit. Prefer this for availability questions.
* get_chemical -- full record for one CAS: GHS/H/P hazard codes, storage
  class, SDS link, vendor/lot/expiry per bottle.
* inventory_stats -- totals plus per-group (per-lab) bottle counts.

Inventory is read-only here: you cannot add, deduct, or remove stock — that
happens through bitácora's own inventory page.

You cannot actuate hardware. If the user asks you to, say so and offer to
investigate the relevant logs/history instead.

You have no access to experiment results (run records, per-well outcomes) —
that is deliberate, not an outage. If asked, say so and point the operator
at the dashboard's History tab.

Be terse. Operators are glancing at a small chat panel, not reading prose.

* Default to 1-3 sentences. Stretch only when the user explicitly asks for
  detail.
* Show the answer first; skip preamble like "I checked X and Y" or "Let me
  look into that".
* Use a short bulleted list only when the answer is genuinely a list of 3+
  items. Otherwise, prose.
* When you cite history, include the device_id and a relative time
  ("3 hours ago"). If the data does not answer the question, say so plainly
  rather than speculate."""


# Appended to SYSTEM_PROMPT only when the operator is in Control mode and a
# verified actor is bound. Even then, no tool actuates hardware — see
# assistant_control.py.
CONTROL_PROMPT_ADDENDUM = """

CONTROL MODE IS ACTIVE.
You may now PROPOSE equipment actions for the operator to authorize, using
the lab-control tools: one action (propose_action) or an ORDERED multi-step
plan on one device (propose_plan). You still cannot actuate hardware
yourself: a proposal only renders a confirm card that a human must read and
click.

HARD RULES FOR EVERY CONTROL REQUEST (do not skip):

1. EVERY REPLY ENDS WITH EXACTLY ONE lab-control TERMINAL CALL. The three
   terminal tools are propose_action, propose_plan, and decline_proposal —
   one of them must be the last tool you call before your reply ends, every
   single control-mode turn, with no exception for informational exchanges:
   (a) propose_action / propose_plan — the ONLY thing that renders the
       authorize button. TEXT NEVER RENDERS A BUTTON: writing "I've proposed
       it, click Authorize" without having called the tool shows the
       operator nothing. If you did not call the tool, there is no button.
   (b) decline_proposal(reason_code, explanation) — when you will not or
       cannot propose. reason_code is one of: not_proposable, safety_floor,
       cross_device, too_many_steps, needs_human, device_unavailable,
       unsafe_state, informational (a purely informational exchange with no
       action to propose), other. explanation is ONE line the operator reads
       on screen; still give the fuller reasoning in your text.
   A propose call that comes back REFUSED (an error+code result) also ends
   the turn: the refusal is shown to the operator automatically — relay the
   reason in your text, do not retry a workaround, and do not follow it with
   decline_proposal. Prose alone never ends a control-mode turn; a reply
   without a terminal call is a protocol violation — the harness bounces it
   back to you once, then reports the failure to the operator.
2. SURFACE STATE CONFLICTS BEFORE THE BUTTON. Before proposing, read the
   device's live state (list_available_actions / get_equipment_status) and
   check the action against it. If what the user asked would conflict with
   the state machine — action not currently allowed, would 412, a required
   labware/tip rack/plate is NOT declared or loaded on the target slot, the
   device is not initialized, activity is in flight, etc. — STATE THE
   CONFLICT EXPLICITLY in your reply text BEFORE (or alongside) the propose
   call, and make the concern the operator's problem to resolve. It is normal
   and expected that a workflow does not pre-declare every labware: do not
   silently proceed as if absent labware is fine, and do not refuse to help.
   Instead, name the missing prerequisite and either propose the corrective
   step first (e.g. deck.declare / setup / plate.load / tip rack declare) as
   the leading step of the plan, or tell the user the action will fail until
   it is resolved. A bare proposal that the device will refuse is a bad
   outcome; an unexplained refusal is equally bad. Always land on: propose
   (raising any conflict first) OR a stated reason.

When the user asks you to make a device do something:
1. Call list_available_actions(equipment_id) to see what the device currently
   allows and which actions are proposable (each with its argument schema).
   Use list_equipment_now first if you need the canonical equipment_id.
2. Call propose_action(equipment_id, action, args, reason) for ONE action
   on ONE device, or propose_plan(equipment_id, steps, reason) for an
   ORDERED sequence of steps on ONE device. When the user wants more than
   one step on the same device, call propose_plan ONCE with every step in
   order instead of a chain of single proposals. `action` must be a string
   from the device's live allowed_actions — for a plan only the FIRST step
   must be allowed right now; later steps are checked live by the device as
   they run. `reason` is a short human-facing justification. The operator
   approves a plan's step list as shown, then runs it; the browser sends the
   steps in order and stops at the first one the device refuses (the rest
   are skipped, never continued past an error). One device per plan — if
   the work spans devices or exceeds the step cap, say so and recommend a
   validated workflow plan.

list_available_actions marks which advertised actions are proposable.
Safety-floor actions must stay reachable without you and are never
proposable: the xArm's stop / connect / clear_errors, and every device's
stop verb (sash.stop, shake.stop, the press's stop, the PlateLoc's
seal.stop). If the user wants something stopped, point them at the
device's operator control — do not propose an alternative action to
"work around" a stop.

Proposable kinds beyond the xArm: the OT-2 (liquid_handler), the fume hood
(sash.move), the shaker (startup, shutdown, shake.start,
shake.set_temperature, shake.set_speed), the press (init, press.up,
press.down, plate.in, plate.out), cameras (ptz, preset/save, preset/goto,
privacy, streaming), the Cytation plate reader (finite lifecycle, drawer,
plate-record, read, imaging, and incubator.set_temperature), and the PlateLoc sealer
(startup, shutdown, stage.in, stage.out, seal.set_temperature,
seal.set_time, seal.start). The HPLC is NOT proposable at all: its queue,
campaign-lock, and standby verbs stay operator/workflow-only, so answer
HPLC control requests by pointing at the operator surfaces instead.

For the press (kind press, equipment_id filter_every_well, shown as
"Waters Filtration"): action names are dotted — press.up, press.down,
plate.in, plate.out, init — never the slash form the browser POSTs
(press/up). press.up and press.down re-energise the pneumatic valve for
hold_time even when the platen is already in that pose. If the user asks
to press up or down and the action is advertised, call propose_action —
do not skip because the status message already says UP or DOWN, and do
not refuse a single named move as "out of cycle order". A full
filtration cycle is plate.in → press.down → press.up → plate.out, proposed
as ONE plan with those four steps, only when the user asks for a cycle. hold_time is 0–10 s;
if the user does not name one, pass 2 for press.up and 5 for press.down
(the tile defaults) so the confirm card shows the duration.

For the Cytation (kind plate_reader), use the live schemas for startup,
shutdown, drawer.open, drawer.close, plate.load, plate.unload, well.update,
read.absorbance, read.fluorescence, read.luminescence, imaging.capture, and
incubator.set_temperature. Read methods do NOT accept gain; imaging.capture
gain is camera analog gain in dB and is a different field. The incubator
argument is `celsius` (18-65), not `temperature_c`. Never propose
shake.start: Cytation shaking has no duration timer and needs a later
shake.stop, so the complete start/use/stop sequence belongs in a
human-authorized workflow. Never propose incubator.stop or shake.stop;
they are safety-floor controls.

For the PlateLoc (kind plate_sealer), use the live schemas for startup,
shutdown, stage.in, stage.out, seal.set_temperature, seal.set_time, and
seal.start. A seal cycle is one complete act: the device times it
(0.5–12 s) and withholds seal.start until the heater is in band and the
stage is in, so the confirm card may carry the complete temperature_c
and seconds values. If the action is not advertised, say so rather than
proposing it anyway. Stage in → seal.start → stage out is a sequence:
propose it as ONE plan (propose_plan) in that order — the device withholds
seal.start until the stage is in and the heater is in band, and the run
stops there if it is not. Never propose seal.stop; it is a safety-floor
control.

Cameras are convenience controls (cannot damage hardware or a sample), but a
confirm card is still required for every PTZ nudge, preset, and privacy/
streaming toggle — never claim you moved a camera yourself. `ptz` takes one
discrete nudge (direction, optional speed 0-1, optional duration_ms); for
"look left/right/up/down" or "zoom in/out" requests propose one nudge at a
time rather than guessing a large duration. Use preset/goto for a named
saved position instead of nudging blindly if one exists — check
get_equipment_status's details.presets first. A camera with no ONVIF PTZ
service (a fixed lens) will not advertise ptz/preset actions at all; say so
rather than proposing one anyway.

On the OT-2 the full control surface is proposable, under two disciplines:

- Some argument fields are operator-only and never yours to set: startup's
  password / host_alias (the gateway supplies its own from service env),
  pick_up_tip's force (cross-contamination-guard override), move_to's
  force_direct (collision-safe-path override). They are omitted from the
  schemas you are shown; supplying one refuses the whole proposal. Never ask
  the user to paste a device credential into chat.
- Liquid handling is sequence-bound (pick_up_tip -> aspirate -> dispense ->
  drop_tip). Propose the whole sequence as ONE plan with propose_plan, steps
  in the correct order; the operator reviews and approves the list as shown,
  then runs it, and the device re-checks each step live as it is sent — the
  run stops at the first refusal and the remaining steps are skipped. Use a
  single propose_action only when the user asks for one step. If the work
  exceeds the step cap or spans devices, say so and recommend a validated
  workflow plan.
- Labware and pipette NAMES come from the run, not from the recipe. Resolve
  every labware_nickname / pipette against get_equipment_status's
  details.snapshot.labwares and details.snapshot.pipettes — those are the run
  engine's real ids and the only names pick_up_tip / aspirate / dispense /
  move_to / drop_tip can resolve. details.session_recipe is NOT a substitute:
  the gateway records it BEFORE the setup runs and never rolls it back, so
  after a failed setup it advertises labware that was never loaded (seen live
  2026-08-27: the recipe said tiprack9/plate1 while the run held
  tiprack300/plate_slot1, and every plan built from the recipe died on
  "409 labware 'tiprack9' is not loaded in this run"). When the two disagree,
  believe the snapshot, use its ids, and tell the operator they diverged.
- A refused action LATCHES the OT-2. Any /control/* failure puts the gateway
  in equipment_status error, which drops setup / pick_up_tip / aspirate /
  dispense out of allowed_actions; the only recovery is the operator clicking
  CLEAR ERROR (reconcile) in the gateway panel, which you cannot propose. So a
  proposal the device will refuse costs a human intervention, not just a
  retry — check the names against the snapshot first. Relatedly, never include
  the slot-12 fixed trash in a setup recipe: the gateway registers it at
  startup and slot 12 is an addressable area, not loadable labware, so the
  entry fails and leaves the run half-loaded.
- deck.declare and setup are NOT interchangeable for custom labware — they do
  different things and only one of them makes a custom plate actually usable:
  * deck.declare is METADATA ONLY. It records intent for /status display and
    for other tiles/tools to read. It does NOT register anything with the
    robot's run engine, and — even when its definition field is attached —
    it has NO effect on whether pick_up_tip/aspirate/dispense/move_labware
    will later work on that slot. The gateway's lazy per-action auto-load
    always treats a declared slot as a STANDARD built-in Opentrons labware;
    it can never load a custom definition into a live run, no matter what
    was declared.
    * setup is what actually loads labware into the live run engine, and is
    the ONLY path that can make a custom labware pickable/usable. Its
    labware[] entries need ot_default:false and the full definition under
    config for any custom load_name.
  Rule of thumb: propose deck.declare only to correct/set the deck's
  DISPLAYED metadata (e.g. fixing a slot that shows kind "unknown"). Propose
  setup — not deck.declare — whenever the operator actually wants to use a
  custom labware in an upcoming pick_up_tip/aspirate/dispense/move_labware
  sequence.
- Before proposing deck.declare or setup for a load_name that is not a
  standard built-in Opentrons definition (anything not matching the
  `<brand>_<count>_<category>_...` pattern of an official catalog name, e.g.
  a lab-specific plate or rack), call lookup_custom_labware(load_name) FIRST
  and include the returned "definition" object in the proposal (deck.declare:
  {"load_name":..., "definition":...} per slot; setup: the matching labware[]
  entry's config, with ot_default:false). A bare load_name for custom labware
  silently resolves to unusable geometry on the gateway (kind "unknown", no
  grid) — never propose one without the definition attached. If
  lookup_custom_labware returns unknown_labware, tell the operator the
  labware needs to be uploaded to the dashboard's labware store first; do not
  fabricate a definition.

On the robot arm (xArm), moves are constrained to a motion graph and only
single hops from the current node are advertised (move.<node_id>).
list_available_actions also returns the device's read-only motion_graph
snapshot: current_node, reachable_nodes (the single-hop targets), and
travel_targets (nodes reachable in 2+ hops). Use it to plan and explain a
route, then propose the route as ONE plan of move.<node_id> hops in order
(propose_plan); the device whitelists each hop live as it is sent, so a hop
that is no longer reachable stops the run there. If a target is in
travel_targets but not reachable_nodes, route through the intermediate hop
rather than calling the move impossible.

The arm's gripper works the same way: transitions are whitelisted per node and
per current stroke, and each legal one is advertised as gripper.<state> (e.g.
gripper.grip_120) — the same names as motion_graph.allowed_gripper_targets. The
arm must be parked, so a gripper action is never advertised mid-move. Picking a
plate up is therefore a sequence — move to the pick position, then the grip,
then move away — so propose it as one plan (move, gripper, move), and never
describe the gripper as uncontrollable when a gripper.<state> action is
listed.

Operator-only is a property of the action or field, never of who is asking:
do not imply the user lacks permission, and do not describe a proposable
action as needing an operator — every proposal does, that is the point. If a
proposal is refused, relay the reason plainly — never try to route around it."""


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class PlanApproveRequest(BaseModel):
    """``POST /api/assistant/plans/{id}/approve`` — the human gate (Step 1i).

    ``step_hash`` is what the operator was *shown*. Requiring them to send it
    back is what makes this a review rather than a rubber stamp: if the plan
    changed (or the dashboard restarted and the id is meaningless), the
    approval is refused instead of silently applying to different steps."""

    step_hash: str = Field(min_length=16, max_length=128)


class PlanStepResult(BaseModel):
    index: int = Field(ge=1)
    outcome: Literal["ok", "failed", "skipped"]
    status_code: int | None = None
    message: str | None = Field(default=None, max_length=500)


class PlanFinishRequest(BaseModel):
    """``POST /api/assistant/plans/{id}/finish`` — how the run ended, reported
    by the browser that ran it. Audit only: the per-step ``control_action``
    rows already exist (stamped with the plan ref); this row says who agreed
    to what and how far it got."""

    status: Literal["executed", "failed", "aborted"]
    results: list[PlanStepResult] = Field(default_factory=list, max_length=64)
    halt_reason: str | None = Field(default=None, max_length=500)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    # UI_DESIGN §5: "ask" (default, read-only) or "control" (propose-only).
    # The server decides the actual toolset from the verified identity — a
    # client that lies about its mode gains nothing.
    mode: Literal["ask", "control"] = "ask"


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8")


# Opening SSE comment (see the chat route's ``gen``): 2 KiB clears Caddy's
# 512-byte encode threshold with room for other proxies' larger ones.
SSE_PREAMBLE = b": " + b" " * 2048 + b"\n\n"


def _format_prompt(messages: list[ChatMessage]) -> str:
    """Render the conversation as a single prompt string.

    Claude Code's --print mode takes one prompt; we don't manage session-id
    state, so the prior turns are inlined verbatim and the latest user
    message ends the prompt. The role markers are conventional enough that
    Claude reliably treats them as a conversation transcript.
    """

    if len(messages) == 1:
        return messages[0].content
    lines: list[str] = ["Conversation so far:"]
    for m in messages[:-1]:
        marker = "User" if m.role == "user" else "Assistant"
        lines.append(f"\n{marker}: {m.content}")
    lines.append("\n\nNew user message:\n" + messages[-1].content)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rate-limit interpretation
# ---------------------------------------------------------------------------


def _format_reset(epoch: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime(
            "%H:%M UTC"
        )
    except (TypeError, ValueError, OSError):
        return None


def _rate_limit_block_message(info: dict[str, Any] | None) -> str | None:
    """Return a human-readable cause string when a ``rate_limit_event``
    indicates the request was *blocked*.

    A ``status`` of ``"allowed"`` is normal -- the success path also emits a
    ``rate_limit_event`` (often with ``overageStatus: "rejected"`` when the
    account has no overage budget) -- so only a non-allowed status counts as
    the reason a turn failed.
    """

    if not isinstance(info, dict):
        return None
    status = info.get("status")
    if status in (None, "allowed"):
        return None
    reset = _format_reset(info.get("resetsAt"))
    if info.get("overageDisabledReason") == "out_of_credits":
        base = "Claude is out of credits and overage is disabled"
    else:
        kind = info.get("rateLimitType") or "usage"
        base = f"Claude {kind} limit reached"
    return base + (f"; resets at {reset}." if reset else ".")


# ---------------------------------------------------------------------------
# stream-json event -> SSE frame translation
# ---------------------------------------------------------------------------


def _json_payloads_from_tool_result(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Every JSON object a tool_result block carries.

    Claude relays a tool result's content either as a plain string or as a
    list of ``{"type":"text","text":...}`` blocks; handle both. Claude Code
    also wraps MCP tool output as ``{"result": "<json string>"}`` while other
    builds pass the tool's JSON through unchanged — the envelope is a CLI
    implementation detail, not a contract, so both are accepted."""

    content = block.get("content")
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                texts.append(c["text"])
    payloads: list[dict[str, Any]] = []
    for text in texts:
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        # Claude Code wraps MCP tool output as {"result": "<json string>"};
        # other builds pass the tool's JSON through unchanged. The envelope is
        # a CLI implementation detail, not a contract, so accept both rather
        # than pinning to whichever one this host happens to emit.
        if isinstance(data, dict) and isinstance(data.get("result"), str):
            try:
                data = json.loads(data["result"])
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(data, dict):
            payloads.append(data)
    return payloads


def _proposal_from_tool_result(block: dict[str, Any]) -> dict[str, Any] | None:
    """The control proposal in a tool_result block, if any.

    ``lab-control``'s ``propose_action`` returns ``{"proposal": {...}}``.
    Returns the inner dict, or None for any other tool result (including
    refusals, which carry ``error``/``code`` instead)."""

    for data in _json_payloads_from_tool_result(block):
        if isinstance(data.get("proposal"), dict):
            return data["proposal"]
    return None


def _plan_from_tool_result(block: dict[str, Any]) -> dict[str, Any] | None:
    """Step 1i sibling of :func:`_proposal_from_tool_result`:
    ``propose_plan`` returns ``{"plan": {...}}``."""

    for data in _json_payloads_from_tool_result(block):
        if isinstance(data.get("plan"), dict):
            return data["plan"]
    return None


def _refusal_from_tool_result(block: dict[str, Any]) -> dict[str, Any] | None:
    """A lab-control proposal refusal in a tool_result block, if any.

    ``propose_action`` / ``propose_plan`` refuse with ``{"error": <msg>,
    "code": <code>}`` where ``code`` is one of
    ``assistant_control.REFUSAL_CODES``. Step 1j surfaces these as a
    dedicated ``proposal_refused`` frame: a refusal with no frame is the
    operator's "the assistant understood but no authorize button appeared"
    complaint — the why lived only in prose the model may or may not write.
    Matching on the code set keeps ordinary history-tool errors (which carry
    no such code) out of the refusal chip."""

    for data in _json_payloads_from_tool_result(block):
        code = data.get("code")
        if isinstance(data.get("error"), str) and code in REFUSAL_CODES:
            return {"code": code, "message": data["error"]}
    return None


def _declined_from_tool_result(block: dict[str, Any]) -> dict[str, Any] | None:
    """The explicit no-proposal terminal (Step 1j): ``decline_proposal``
    returns ``{"declined": {"reason_code": ..., "explanation": ...}}``."""

    for data in _json_payloads_from_tool_result(block):
        if isinstance(data.get("declined"), dict):
            return data["declined"]
    return None


def _translate_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert one ``claude -p --output-format stream-json`` line into zero
    or more frames suitable for the AssistantBubble SSE consumer.

    The Bubble understands ``status`` (phase of a stretch that produces no
    visible token), ``text`` (token delta), ``tool_use`` (model is about to
    call a tool), ``tool_result`` (tool returned), ``done``, and ``error``.
    Everything else from claude-code's richer event taxonomy is dropped on
    the floor.
    """

    out: list[dict[str, Any]] = []
    etype = event.get("type")

    if etype == "stream_event":
        inner = event.get("event") or {}
        itype = inner.get("type")
        if itype == "content_block_delta":
            delta = inner.get("delta") or {}
            if delta.get("type") == "text_delta":
                text = delta.get("text") or ""
                if text:
                    out.append({"type": "text", "delta": text})
        elif itype == "content_block_start":
            block = inner.get("content_block") or {}
            if block.get("type") == "tool_use":
                name = block.get("name") or "tool"
                # Strip the "mcp__<server>__" prefix so the bubble shows a
                # short tool name; full name is still in the title attr.
                pretty = name.split("__")[-1] if "__" in name else name
                out.append({"type": "tool_use", "name": pretty})

    elif etype == "user":
        # Sent back to ourselves when a tool result is appended to the
        # transcript. Surface a "tool_result" frame so the Bubble can flip
        # the spinner to a checkmark.
        message = event.get("message") or {}
        content = message.get("content") or []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    # We don't have the tool name on the result block, but
                    # the Bubble's match-most-recent logic accepts any name
                    # so we pass through "tool" if absent.
                    out.append({"type": "tool_result", "name": "tool"})
                    # Control returns to the model, which now thinks before
                    # its next visible token. Without this the bubble goes
                    # blank again between every tool call.
                    out.append({"type": "status", "phase": "thinking"})
                    # Control mode: a propose_action result carries a validated
                    # proposal. Surface it as a dedicated frame so the Bubble
                    # can render a confirm card the operator must authorize.
                    proposal = _proposal_from_tool_result(block)
                    if proposal is not None:
                        out.append({"type": "proposal", "proposal": proposal})
                    plan = _plan_from_tool_result(block)
                    if plan is not None:
                        out.append({"type": "plan", "plan": plan})
                    # Step 1j: a refused proposal and an explicit decline are
                    # both terminal outcomes the operator must SEE — the
                    # button's absence alone is not an explanation.
                    refusal = _refusal_from_tool_result(block)
                    if refusal is not None:
                        out.append({"type": "proposal_refused", "refusal": refusal})
                    declined = _declined_from_tool_result(block)
                    if declined is not None:
                        out.append({"type": "declined", "declined": declined})

    elif etype == "result":
        # Final wrap-up. is_error=true means a hard failure that didn't
        # produce a usable assistant reply.
        if event.get("is_error"):
            out.append(
                {
                    "type": "error",
                    "message": event.get("result")
                    or event.get("subtype")
                    or "Claude returned an error",
                }
            )
        else:
            out.append({"type": "done"})

    elif etype == "system" and event.get("subtype") == "status":
        status = event.get("status")
        if status == "error":
            out.append({"type": "error", "message": event.get("message") or "claude error"})

    return out


# ---------------------------------------------------------------------------
# Subprocess driver
# ---------------------------------------------------------------------------


async def _run_claude(
    messages: list[ChatMessage],
    *,
    control: bool = False,
    actor: str | None = None,
    on_proposal: "Callable[[dict[str, Any]], Awaitable[None]] | None" = None,
    on_plan: "Callable[[dict[str, Any]], Awaitable[None]] | None" = None,
) -> AsyncIterator[bytes]:
    binary = _claude_binary()
    if binary is None:
        yield _sse(
            {
                "type": "error",
                "message": (
                    "claude CLI not found on PATH. Install Claude Code or set "
                    "ASSISTANT_CLAUDE_BIN to its full path."
                ),
            }
        )
        return

    include_control = control and bool(actor)
    model = CONTROL_MODEL if include_control else DEFAULT_MODEL
    prompt = _format_prompt(messages)
    mcp_config_path = _write_mcp_config(include_control=include_control, actor=actor)
    system_prompt = SYSTEM_PROMPT + (CONTROL_PROMPT_ADDENDUM if include_control else "")
    allowed_tools = f"{ALLOWED_TOOL_GLOB} {INVENTORY_TOOL_GLOB}"
    if include_control:
        allowed_tools = f"{allowed_tools} {CONTROL_TOOL_GLOB}"
    args = [
        binary,
        "--print",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--verbose",  # required alongside stream-json
        "--no-session-persistence",
        "--append-system-prompt",
        system_prompt,
        "--mcp-config",
        str(mcp_config_path),
        "--strict-mcp-config",
        "--allowedTools",
        allowed_tools,
        "--model",
        model,
        "--permission-mode",
        "default",
        prompt,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=_claude_cwd(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # stream-json is one JSON object per line, and a single tool_result
            # event carries the whole tool payload — an OT-2 deck/tip snapshot
            # alone clears asyncio's 64 KiB default, which readline() answers
            # with "Separator is found, but chunk is longer than limit".
            limit=10 * 1024 * 1024,
        )
    except FileNotFoundError:
        yield _sse({"type": "error", "message": f"could not spawn {binary}"})
        return

    assert proc.stdout is not None

    timeout_handle: asyncio.TimerHandle | None = None
    timed_out = False

    def _on_timeout() -> None:
        nonlocal timed_out
        timed_out = True
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    loop = asyncio.get_running_loop()
    timeout_handle = loop.call_later(DEFAULT_TIMEOUT_S, _on_timeout)

    # Before any CLI output: process spawn, MCP server handshakes, then the
    # model's first think. Announce the phase so the bubble shows a live pill
    # for that stretch instead of an empty turn.
    yield _sse({"type": "status", "phase": "thinking"})

    last_rate_limit: dict[str, Any] | None = None
    saw_terminal = False  # did we already yield a done/error frame?
    result_info: dict[str, Any] | None = None  # the CLI's final "result" event
    started = time.monotonic()

    try:
        while True:
            try:
                line = await proc.stdout.readline()
            except asyncio.CancelledError:
                # Client disconnected. Kill the subprocess so it doesn't
                # keep burning quota on a response no one will see.
                if proc.returncode is None:
                    proc.kill()
                raise
            if not line:
                break
            try:
                event = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                logger.debug("non-JSON line from claude: %s", line[:200])
                continue
            if event.get("type") == "rate_limit_event":
                info = event.get("rate_limit_info")
                if isinstance(info, dict):
                    last_rate_limit = info
            elif event.get("type") == "result":
                result_info = event
            for frame in _translate_event(event):
                if frame.get("type") in ("done", "error"):
                    saw_terminal = True
                if frame.get("type") == "proposal" and on_proposal is not None:
                    proposal = frame.get("proposal")
                    if isinstance(proposal, dict):
                        try:
                            await on_proposal(proposal)
                        except Exception:  # noqa: BLE001 - audit must not break the stream
                            logger.warning("assistant_proposal audit failed", exc_info=True)
                if frame.get("type") == "plan" and on_plan is not None:
                    plan = frame.get("plan")
                    if isinstance(plan, dict):
                        try:
                            await on_plan(plan)
                        except Exception:  # noqa: BLE001 - audit must not break the stream
                            logger.warning("assistant_plan audit failed", exc_info=True)
                yield _sse(frame)
    finally:
        if timeout_handle is not None:
            timeout_handle.cancel()
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        stderr_bytes = b""
        if proc.stderr is not None:
            try:
                stderr_bytes = await proc.stderr.read()
            except Exception:  # noqa: BLE001
                pass
        # One completion line per turn so latency and account burn are
        # observable in the journal (the start line logs who asked; this one
        # logs what it cost). Runs on every exit path, including client
        # disconnect and timeout.
        usage = (result_info or {}).get("usage") or {}
        logger.info(
            "assistant turn done: user=%s mode=%s elapsed=%.1fs num_turns=%s "
            "api_ms=%s tokens_out=%s cache_read=%s rc=%s timed_out=%s "
            "rate_limit=%s backend=claude-cli model=%s",
            actor or "unauthenticated(dev-open)",
            "control" if include_control else "ask",
            time.monotonic() - started,
            (result_info or {}).get("num_turns"),
            (result_info or {}).get("duration_api_ms"),
            usage.get("output_tokens"),
            usage.get("cache_read_input_tokens"),
            proc.returncode,
            timed_out,
            (last_rate_limit or {}).get("status"),
            model,
        )

    if timed_out:
        yield _sse(
            {
                "type": "error",
                "message": f"claude exceeded {DEFAULT_TIMEOUT_S:.0f}s timeout",
            }
        )
        return
    # If a terminal frame already went out (normal done, or an error the model
    # reported via the result event), don't double-report on exit code.
    if not saw_terminal and proc.returncode and proc.returncode != 0:
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")[-2000:].strip()
        rate_limit_msg = _rate_limit_block_message(last_rate_limit)
        logger.warning(
            "claude exited %s (rate_limit=%s): %s",
            proc.returncode,
            last_rate_limit,
            stderr_text,
        )
        if rate_limit_msg:
            message = rate_limit_msg
        elif stderr_text:
            message = f"claude exited {proc.returncode}: {stderr_text}"
        else:
            message = (
                f"claude exited {proc.returncode} with no error output — "
                "check `journalctl -u ac-organic-lab-api` on the dashboard host."
            )
        yield _sse({"type": "error", "message": message})


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def build_assistant_router() -> APIRouter:
    router = APIRouter(prefix="/api/assistant", tags=["assistant"])

    # Step 1i: plans the assistant proposed, keyed by plan_id, held only until
    # their TTL runs out. In memory and gone on restart — deliberately: an
    # approval is a review of one moment, and an id minted before a restart
    # must not be approvable after it (mirrors opentrons-server's PlanStore).
    # What the store buys is that the approve route can check the hash the
    # operator sends against the plan the tool actually produced (409 on a
    # mismatch), and that the audit rows name the same plan end to end.
    plans: dict[str, dict[str, Any]] = {}

    def _sweep_plans() -> None:
        now = time.monotonic()
        for pid in [pid for pid, rec in plans.items() if rec["expires_at"] <= now]:
            plans.pop(pid, None)

    def _plan_record(plan_id: str, actor: str | None) -> dict[str, Any]:
        """The live record for ``plan_id``, or the right refusal. The acting
        identity must be the one the plan was proposed to: X-Auth-User is set
        by the middleware after verifying the session, never by the client."""

        _sweep_plans()
        if not actor:
            raise HTTPException(status_code=401, detail="Sign in to approve a plan.")
        rec = plans.get(plan_id)
        if rec is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "unknown plan — it expired, was already finished, or the "
                    "dashboard restarted since it was proposed; ask again for a fresh one"
                ),
            )
        if rec["actor"] != actor:
            raise HTTPException(
                status_code=403, detail="this plan was proposed to a different operator"
            )
        return rec

    @router.get("/health")
    async def health() -> dict[str, Any]:
        from . import assistant_openai

        binary = _claude_binary()
        ask_openai = DEFAULT_BACKEND == "openai"
        ctl_openai = CONTROL_BACKEND == "openai"
        # "configured" gates whether the bubble renders at all, so it reports
        # the Ask-mode backend's readiness (Ask is the default surface).
        configured = (
            assistant_openai.api_key() is not None if ask_openai else binary is not None
        )
        return {
            "configured": configured,
            "backend": DEFAULT_BACKEND,
            "control_backend": CONTROL_BACKEND,
            "binary": binary,
            "model": assistant_openai.OPENAI_MODEL if ask_openai else DEFAULT_MODEL,
            "control_model": (
                assistant_openai.OPENAI_CONTROL_MODEL if ctl_openai else CONTROL_MODEL
            ),
            "allowed_tools": f"{ALLOWED_TOOL_GLOB} {INVENTORY_TOOL_GLOB}",
            "cwd": _claude_cwd(),
        }

    @router.post("/chat")
    async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
        # Attribution (Phase 2): X-Auth-User is set by the Next.js middleware
        # after verifying the session — never client-supplied. The backend
        # Claude account is shared, so who-asked lives in this log line.
        actor = request.headers.get("x-auth-user")

        # Control mode is only honoured for a verified actor, and never under
        # the DASHBOARD_CONTROL_OPEN dev bypass (which has no identity to bind
        # a proposal to). The client's requested mode is advisory; the server
        # decides the toolset. Per-equipment authorization is enforced inside
        # the lab-control server's propose_action, against this same actor.
        control_open = os.environ.get("DASHBOARD_CONTROL_OPEN") == "true"
        control = body.mode == "control" and bool(actor) and not control_open

        backend = CONTROL_BACKEND if control else DEFAULT_BACKEND
        if backend == "openai":
            from . import assistant_openai

            if assistant_openai.api_key() is None:
                raise HTTPException(
                    status_code=503,
                    detail="ASSISTANT_OPENAI_API_KEY is not set on the dashboard host",
                )
            runner = assistant_openai.run_openai_turn
        else:
            if _claude_binary() is None:
                raise HTTPException(
                    status_code=503,
                    detail="claude CLI is not installed on the dashboard host",
                )
            runner = _run_claude

        logger.info(
            "assistant chat: user=%s mode=%s->%s backend=%s messages=%d",
            actor or "unauthenticated(dev-open)",
            body.mode,
            "control" if control else "ask",
            backend,
            len(body.messages),
        )

        db = getattr(request.app.state, "db", None)

        async def record_proposal(proposal: dict[str, Any]) -> None:
            """Audit the proposal itself (not just the eventual click) so the
            trail shows what talked the operator into authorizing. Best-effort;
            never blocks or breaks the stream. Paired with the ``origin`` field
            on the later ``control_action`` row (control.py)."""

            equipment_id = str(proposal.get("equipment_id") or "unknown")
            await _audit(
                db,
                equipment_id,
                "assistant_proposal",
                f"assistant proposed {proposal.get('action')} on {equipment_id} to {actor}",
                {
                    "actor": actor,
                    "action": proposal.get("action"),
                    "passthrough_action": proposal.get("passthrough_action"),
                    "args": proposal.get("args"),
                    "reason": proposal.get("reason"),
                    "device_state": proposal.get("device_state"),
                },
            )

        async def record_plan(plan: dict[str, Any]) -> None:
            """Step 1i: cache the plan for the approve/finish routes and audit
            it. Only a plan whose hash the dashboard can recompute from its
            own steps, and that names this request's actor, is cached — a
            tool result that fails either is logged and left un-approvable
            (the card still renders, Approve then 404s)."""

            plan_id = plan.get("plan_id")
            steps = plan.get("steps")
            if (
                not isinstance(plan_id, str)
                or not plan_id
                or not isinstance(steps, list)
                or not steps
                or plan.get("actor") != actor
                or plan.get("step_hash") != plan_step_hash(steps)
            ):
                logger.warning(
                    "assistant_plan ignored: malformed or mis-attributed plan %r", plan_id
                )
                return
            _sweep_plans()
            ttl = float(plan.get("expires_in_s") or PLAN_TTL_S)
            plans[plan_id] = {
                "plan": plan,
                "actor": actor,
                "expires_at": time.monotonic() + ttl,
                "approved_at": None,
            }
            equipment_id = str(plan.get("equipment_id") or "unknown")
            await _audit(
                db,
                equipment_id,
                "assistant_plan_proposed",
                f"assistant proposed a {len(steps)}-step plan on {equipment_id} to {actor}",
                {
                    "actor": actor,
                    "plan_id": plan_id,
                    "step_hash": plan.get("step_hash"),
                    "steps": steps,
                    "reason": plan.get("reason"),
                    "device_state": plan.get("device_state"),
                },
            )

        async def gen() -> AsyncIterator[bytes]:
            # Comment frame, ignored by the bubble (no `data:` line), sized to
            # push the response past any intermediary's "buffer until N bytes
            # before deciding whether to compress" threshold — Caddy's encode
            # holds the first 512 bytes and swallows flushes until then. The
            # hold would otherwise eat the first several status pills.
            yield SSE_PREAMBLE
            try:
                async for frame in runner(
                    body.messages,
                    control=control,
                    actor=actor,
                    on_proposal=record_proposal if control else None,
                    on_plan=record_plan if control else None,
                ):
                    yield frame
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("assistant stream errored")
                yield _sse({"type": "error", "message": str(exc)})

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                # `no-transform` is load-bearing, not boilerplate. Next.js's
                # rewrite proxy runs its default gzip `compression` over this
                # response, and that middleware buffers text/event-stream in
                # zlib until the stream ENDS when the browser sends
                # Accept-Encoding: gzip (it always does). Every progress pill
                # and text delta then arrives in one burst with `done` — the
                # "no thinking progress shown" symptom. `compression` honours
                # RFC 7234 no-transform and passes the stream through
                # untouched; curl without Accept-Encoding never showed the
                # problem, which is why it survived local testing.
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/plans/{plan_id}/approve")
    async def approve_plan(
        plan_id: str, request: Request, body: PlanApproveRequest
    ) -> dict[str, Any]:
        """Record the operator's approval of one exact step list (Step 1i).

        The approval is a *review record*, not a permission grant: each step
        the browser then sends is an ordinary passthrough call under the
        operator's own per-equipment authorization, exactly like a tile
        click. What this route adds is the refusal when the hash sent back is
        not the hash of the plan the tool produced (409) — the property that
        makes "a human approved this" mean "this, as shown".
        """

        actor = request.headers.get("x-auth-user")
        rec = _plan_record(plan_id, actor)
        plan = rec["plan"]
        if rec["approved_at"] is not None:
            raise HTTPException(status_code=409, detail="this plan is already approved")
        if body.step_hash != plan["step_hash"]:
            raise HTTPException(
                status_code=409,
                detail="the plan changed since it was displayed — re-read it and approve again",
            )
        rec["approved_at"] = time.time()
        # Running a long plan and reporting back takes a while; keep the record
        # for another TTL from the approval rather than from the proposal.
        rec["expires_at"] = time.monotonic() + PLAN_TTL_S
        equipment_id = str(plan.get("equipment_id") or "unknown")
        await _audit(
            getattr(request.app.state, "db", None),
            equipment_id,
            "assistant_plan_approved",
            f"{actor} approved a {len(plan['steps'])}-step assistant plan on {equipment_id}",
            {
                "actor": actor,
                "plan_id": plan_id,
                "step_hash": plan["step_hash"],
                "steps": plan["steps"],
                "reason": plan.get("reason"),
            },
        )
        return {
            "plan_id": plan_id,
            "step_hash": plan["step_hash"],
            "approved": True,
            "expires_in_s": PLAN_TTL_S,
        }

    @router.post("/plans/{plan_id}/finish")
    async def finish_plan(
        plan_id: str, request: Request, body: PlanFinishRequest
    ) -> dict[str, Any]:
        """Audit how an approved plan ended and retire its record. A draft may
        only be reported ``aborted`` (dismissed); ``executed``/``failed``
        require the approval that let it run."""

        actor = request.headers.get("x-auth-user")
        rec = _plan_record(plan_id, actor)
        plan = rec["plan"]
        if body.status != "aborted" and rec["approved_at"] is None:
            raise HTTPException(status_code=409, detail="this plan was never approved")
        plans.pop(plan_id, None)
        equipment_id = str(plan.get("equipment_id") or "unknown")
        ran = sum(1 for r in body.results if r.outcome == "ok")
        await _audit(
            getattr(request.app.state, "db", None),
            equipment_id,
            "assistant_plan_finished",
            f"assistant plan on {equipment_id} {body.status} for {actor} "
            f"({ran}/{len(plan['steps'])} steps ok)",
            {
                "actor": actor,
                "plan_id": plan_id,
                "step_hash": plan["step_hash"],
                "status": body.status,
                "results": [r.model_dump() for r in body.results],
                "halt_reason": body.halt_reason,
            },
        )
        return {"ok": True}

    return router


async def _audit(
    db: Any,
    equipment_id: str,
    event_type: str,
    message: str,
    payload: dict[str, Any],
) -> None:
    """Append one ``equipment_events`` row. Best-effort and off the event loop
    (the sqlite write runs in a worker thread); a no-op without a DB."""

    if db is None:
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        functools.partial(
            db.record_equipment_event,
            equipment_id,
            event_type,
            message=message,
            payload=payload,
        ),
    )


__all__ = ["build_assistant_router", "SYSTEM_PROMPT", "DEFAULT_MODEL"]
