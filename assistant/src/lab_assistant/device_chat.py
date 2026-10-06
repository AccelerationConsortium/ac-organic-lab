"""The device panel's assistant, run on the server (consolidation plan, steps 3–4).

One tool-free loop, any model: the engine reads the device as the signed-in
user, asks the model once for a structured reply ``{reply, steps |
for_each_well, prelude, epilogue}``, and — when a draft came back — creates
it on the device (``POST <gateway>/plans``), where it is validated, expanded,
stored, and later approved and run in the panel. This is the loop the OT-2
gateway's own Claude path ran (``gateway/assistant.py::_chat_claude_events``),
moved here and made model-agnostic, and it is UI_DESIGN §2.3's "host the
translation centrally, keep resolution/execution in the gateway": the model
has no tools; the engine does the reads and the one write.

Backends and the boundary they keep:

* **claude-cli** — ``claude -p --json-schema … --tools "" --strict-mcp-config
  --safe-mode``: no built-in tools, no MCP, structured output.
* **openrouter** — one chat completion with ``response_format`` json_schema;
  no tools in the request.
* **codex** is *not* offered. ``codex exec -s read-only`` still gives the model
  a shell that reads any file the service user can (verified 2026-10-05:
  ``head -n 1 /etc/hostname`` ran), and no feature flag removes it. It can be
  added behind a dedicated unprivileged runner account; see the plan doc.

Identity: the route runs behind the dashboard middleware (session required,
``X-Auth-User`` injected); the engine forwards that user, their role and
roster projects to the gateway with the device's edge secret, so the
gateway's login gate and run-data access rule see the real person. The
device secret is per device and must be configured — never a global fallback.

Schema note: OpenAI-style strict structured output forbids open objects and
requires every property, so step arguments travel as JSON *text*
(``args_json``) and are parsed and validated here before anything reaches
the device.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

import httpx

logger = logging.getLogger("app.assistant_device")

CLAUDE_MODELS = [m.strip() for m in os.environ.get(
    "ASSISTANT_DEVICE_CLAUDE_MODELS", "claude-sonnet-5-5").split(",") if m.strip()]
OPENROUTER_MODELS = [m.strip() for m in os.environ.get(
    "ASSISTANT_DEVICE_OPENAI_MODELS", os.environ.get("ASSISTANT_OPENAI_MODEL", "")).split(",")
    if m.strip()]
TIMEOUT_S = float(os.environ.get("ASSISTANT_DEVICE_TIMEOUT_S", "240"))
RECENT_PLANS = 10
#: Plans up to this many steps are attached with their arguments; longer ones
#: (pattern expansions) as their summary plus the action list.
FULL_STEPS_UP_TO = 60
#: Device document reads (guide, action catalog) change only on a gateway
#: deploy; cached per device this long so a turn costs two live reads.
DOCS_TTL_S = 300.0
_READINESS_TTL_S = 60.0


# ---------------------------------------------------------------------------
# Structured reply
# ---------------------------------------------------------------------------

_STEP = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "args_json": {"type": "string",
                      "description": "the step's arguments as a JSON object, encoded as text; \"{}\" for none"},
    },
    "required": ["action", "args_json"],
    "additionalProperties": False,
}
_TEMPLATE_STEP = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "args_json": {"type": "string", "description": "JSON object text; {well} where the well name goes"},
        "id": {"type": "string", "description": "short id, used by overrides; \"\" if none"},
    },
    "required": ["action", "args_json", "id"],
    "additionalProperties": False,
}
REPLY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "what the operator reads; light Markdown"},
        "steps": {"type": "array", "items": _STEP,
                  "description": "written-out steps; empty when using for_each_well or proposing nothing"},
        "prelude": {"type": "array", "items": _STEP, "description": "steps before the per-well loop"},
        "for_each_well": {
            "type": ["object", "null"],
            "properties": {
                "labware_nickname": {"type": "string"},
                "wells_json": {"type": "string",
                               "description": "a range like \"A1:H12\" as a JSON string, or a JSON array of wells"},
                "order": {"type": "string", "enum": ["column", "row"]},
                "steps": {"type": "array", "items": _TEMPLATE_STEP},
                "overrides_json": {"type": "string",
                                   "description": "JSON object {well: {step_id: partial args}}; \"{}\" for none"},
            },
            "required": ["labware_nickname", "wells_json", "order", "steps", "overrides_json"],
            "additionalProperties": False,
        },
        "epilogue": {"type": "array", "items": _STEP, "description": "steps after the per-well loop"},
    },
    "required": ["reply", "steps", "prelude", "for_each_well", "epilogue"],
    "additionalProperties": False,
}


class ReplyError(ValueError):
    """The model's structured reply could not be turned into a proposal."""


def _parse_json_text(text: Any, where: str, expect: type) -> Any:
    if not isinstance(text, str):
        raise ReplyError(f"{where}: expected JSON text")
    try:
        value = json.loads(text) if text.strip() else ({} if expect is dict else [])
    except json.JSONDecodeError as exc:
        raise ReplyError(f"{where}: invalid JSON ({exc.msg})") from exc
    if not isinstance(value, expect):
        raise ReplyError(f"{where}: expected a JSON {expect.__name__}")
    return value


def _steps(raw: Any, where: str) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        raise ReplyError(f"{where}: expected a list of steps")
    out = []
    for i, step in enumerate(raw, start=1):
        if not isinstance(step, dict) or not isinstance(step.get("action"), str):
            raise ReplyError(f"{where} step {i}: needs an action")
        item: Dict[str, Any] = {"action": step["action"],
                                "args": _parse_json_text(step.get("args_json", "{}"), f"{where} step {i} args", dict)}
        if step.get("id"):
            item["id"] = step["id"]
        out.append(item)
    return out


def proposal_from_reply(structured: Dict[str, Any]) -> tuple[str, Optional[Dict[str, Any]]]:
    """``(reply_text, draft_body_or_None)`` — the body is what the gateway's
    ``POST /plans`` takes, with every JSON-text field parsed."""
    if not isinstance(structured, dict) or not isinstance(structured.get("reply"), str):
        raise ReplyError("the model returned no reply text")
    reply = structured["reply"].strip()
    steps = _steps(structured.get("steps") or [], "steps")
    pattern = structured.get("for_each_well")
    if pattern is None and not steps:
        return reply, None
    if pattern is not None and steps:
        raise ReplyError("the model returned both steps and for_each_well")
    if pattern is None:
        return reply, {"steps": steps}
    if not isinstance(pattern, dict):
        raise ReplyError("for_each_well must be an object")
    wells = _parse_json_text(pattern.get("wells_json"), "for_each_well.wells", (str, list))
    body: Dict[str, Any] = {
        "prelude": _steps(structured.get("prelude") or [], "prelude"),
        "for_each_well": {
            "labware_nickname": pattern.get("labware_nickname"),
            "wells": wells,
            "order": pattern.get("order") or "column",
            "steps": _steps(pattern.get("steps") or [], "for_each_well.steps"),
            "overrides": _parse_json_text(pattern.get("overrides_json", "{}"), "for_each_well.overrides", dict),
        },
        "epilogue": _steps(structured.get("epilogue") or [], "epilogue"),
    }
    return reply, body


# ---------------------------------------------------------------------------
# The device
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Device:
    equipment_id: str
    name: str
    base_url: str
    edge_secret: str


class DeviceConfigError(RuntimeError):
    pass


def device_from_entry(entry: Any) -> Device:
    """A registry entry → the device this chat talks to. The edge secret is
    the variable the entry names, and nothing else: a device without its own
    secret is not served (never a global fallback)."""
    env_name = getattr(entry, "edge_secret_env", None)
    secret = os.environ.get(env_name, "").strip() if env_name else ""
    if not secret:
        raise DeviceConfigError(
            f"{entry.id}: no edge secret configured ({env_name or 'edge_secret_env unset'}); "
            "the panel assistant cannot act as the signed-in user on this device")
    return Device(equipment_id=entry.id, name=getattr(entry, "name", entry.id),
                  base_url=str(entry.base_url).rstrip("/"), edge_secret=secret)


def device_headers(device: Device, *, user: str, role: Optional[str],
                   projects: List[str], pi_projects: List[str]) -> Dict[str, str]:
    """The trusted-edge fast path the gateway accepts: the verified user plus
    the device's secret. Projects come from the roster, resolved here —
    never from anything the browser sent."""
    headers = {"X-Auth-User": user, "X-Edge-Auth": device.edge_secret,
               "X-Auth-Projects": ",".join(sorted(projects)),
               "X-Auth-Pi-Projects": ",".join(sorted(pi_projects))}
    if role:
        headers["X-Auth-Role"] = role
    return headers


_docs_cache: Dict[str, tuple[float, Dict[str, Any], Dict[str, Any]]] = {}


#: Labware definition keys the model never needs: per-well geometry (96
#: entries of x/y/z/depth), well groups, brand, slot offset. Measured on
#: Complexation 2026-10-06: two plates made /status 74 KB, 64 KB of it this.
_DEFINITION_DROP = ("wells", "groups", "brand", "cornerOffsetFromSlot")


def compact_status(status: Dict[str, Any]) -> Dict[str, Any]:
    """``/status`` with each deck labware definition reduced to what a plan
    needs — load name, ordering (well names and traversal order), parameters,
    metadata, outer dimensions, one well's depth/volume/shape — and a
    ``declared`` definition identical to the loaded one replaced by a note.
    Nothing is invented; the full envelope stays on the gateway."""
    out = json.loads(json.dumps(status, default=str))
    slots = (((out.get("details") or {}).get("snapshot") or {}).get("deck") or {}).get("slots") or {}
    for slot in slots.values():
        if not isinstance(slot, dict):
            continue
        loaded = slot.get("labware") if isinstance(slot.get("labware"), dict) else None
        declared = slot.get("declared") if isinstance(slot.get("declared"), dict) else None
        if (loaded and declared and declared is not loaded
                and declared.get("definition") and declared.get("definition") == loaded.get("definition")):
            declared["definition"] = "same as labware.definition"
        for item in (loaded, declared):
            definition = item.get("definition") if item else None
            if not isinstance(definition, dict):
                continue
            wells = definition.get("wells") if isinstance(definition.get("wells"), dict) else {}
            compact = {k: v for k, v in definition.items() if k not in _DEFINITION_DROP}
            compact["well_count"] = len(wells)
            if wells:
                first = next(iter(wells.values()))
                if isinstance(first, dict):
                    compact["well_example"] = {k: first.get(k) for k in
                                               ("depth", "totalLiquidVolume", "shape", "diameter",
                                                "xDimension", "yDimension") if first.get(k) is not None}
            item["definition"] = compact
    return out


async def read_context(client: httpx.AsyncClient, device: Device, headers: Dict[str, str],
                       *, on_read: Callable[[str], Awaitable[None]] | None = None) -> Dict[str, Any]:
    """What the gateway's own assistant read before every turn: status (with
    deck and consumables, compacted), the equipment guide and action catalog
    (cached; they go into the system prompt, see :func:`system_prompt`),
    recent plans as this user (so the gateway's access rule applies), and
    plate summaries for recent plans with readings."""
    async def get(path: str, **kw: Any) -> Any:
        if on_read:
            await on_read(path)
        r = await client.get(f"{device.base_url}{path}", headers=headers, timeout=20, **kw)
        r.raise_for_status()
        return r.json()

    status = compact_status(await get("/status"))
    now = time.monotonic()
    cached = _docs_cache.get(device.equipment_id)
    if cached and now - cached[0] < DOCS_TTL_S:
        docs, actions = cached[1], cached[2]
    else:
        docs, actions = await get("/docs/agent"), await get("/plans/actions")
        _docs_cache[device.equipment_id] = (now, docs, actions)
    plans = await get("/plans")
    recent = []
    for plan in (plans if isinstance(plans, list) else [])[:RECENT_PLANS]:
        steps = plan.get("steps") or []
        view = {k: plan.get(k) for k in (
            "plan_id", "status", "created_at", "created_by", "approved_by", "eln_project",
            "halt_reason", "redacted", "pattern_summary")}
        # Steps with their arguments (so a result can be attributed to a well)
        # for plans of ordinary size; a pattern-expanded plate keeps its
        # server-derived summary and the action list instead.
        if len(steps) <= FULL_STEPS_UP_TO:
            view["steps"] = [{"step": i + 1, "action": s.get("action"), "args": s.get("args")}
                             for i, s in enumerate(steps)]
        else:
            view["actions"] = [s.get("action") for s in steps]
        view["results"] = [{"step": i + 1, "action": r.get("action"), "outcome": r.get("outcome"),
                            "message": r.get("message"), "reading": r.get("reading")}
                           for i, r in enumerate(plan.get("results") or [])]
        recent.append(view)
    reports = []
    for plan in recent:
        if plan.get("redacted") or not any(r.get("reading") for r in plan["results"]):
            continue
        try:
            report = await get("/plans/plate-report", params={"plan_id": plan["plan_id"]})
        except httpx.HTTPError:
            continue
        reports.append({"plan_id": plan["plan_id"], **summarize_report(report)})
    details = status.get("details") or {}
    robot = details.get("robot") or {}
    return {
        "reads": {
            "get_status": status,
            "get_consumables": {k: details.get(k) for k in
                                ("tip_racks", "loaded_plate", "mounted_tips", "pipette_channels")}
                               | {"modules": robot.get("modules")},
        },
        "static": {"list_actions": actions, "get_equipment_docs": docs},
        "current_plans": recent,
        "plate_reports": reports,
    }


def summarize_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """The gateway's ``plate_report.summarize_for_agent``, applied to the
    report as served: stats, per-well mass and deviation, what is missing."""
    wells = report.get("wells") or {}
    return {
        "labware": report.get("labware"),
        "plans": report.get("plans"),
        "stats": report.get("stats"),
        "density_g_per_ml": report.get("density_g_per_ml"),
        "wells": {name: {k: c.get(k) for k in ("mass_g", "deviation_pct", "implied_volume_ul",
                                                "volume_ul", "stable", "status", "repeat_weighings")
                         if isinstance(c, dict) and c.get(k) is not None}
                  for name, c in wells.items()},
        "unattributed_readings": len(report.get("unattributed_readings") or []),
        "excluded_deliveries": len(report.get("excluded_deliveries") or []),
    }


async def create_draft(client: httpx.AsyncClient, device: Device, headers: Dict[str, str],
                       body: Dict[str, Any], *, created_by: str) -> Dict[str, Any]:
    """The one device write: a draft. The gateway validates and expands it."""
    r = await client.post(f"{device.base_url}/plans", headers=headers, timeout=30,
                          json={**body, "created_by": created_by})
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ReplyError(f"the device refused the draft: {detail}")
    return r.json()


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

#: The gateway assistant's own prompt (gateway/assistant.py _SYSTEM_PROMPT),
#: verbatim but for the tool references: this loop has no tools; the reads
#: are attached to the request as JSON. Keep the two in step.
SYSTEM_PROMPT = """\
You are the operator assistant for a single {robot_model} liquid handler ({equipment_name}), \
reached through its gateway. You help with simple, single-robot operations on \
THIS robot only.

What you can do:
- Read the robot's state: status, deck layout, tip racks, loaded plate.
- Propose an ordered plan of control actions for the operator to review.
- Propose bookkeeping corrections when the recorded state and the physical \
deck disagree — `tips.mark` (these wells are full / empty), `tips.reset` (a \
fresh rack went in), `plate.load`, `well.update`, `deck.declare`. These are \
in the catalog like any other action. Propose the correction rather than \
describing it in prose and asking the operator to go and do it by hand.
- Module placement changes require an admin to approve and execute. Preserve \
all module placements when proposing ordinary plate or tip-record edits. The \
module tile has no placement editor; admins use the API or an approved chat plan.
- Use `platebalance.tare` to set a loaded plate's weight as the baseline; \
`platebalance.zero` is for the unloaded balance and has a limited zero range. \
`platebalance.tare` holds the command lock and waits up to 30 s for two fresh \
stable near-zero readings, with no gateway robot movement during that wait, \
resending tare and waiting again up to three times if the baseline settles \
off zero. It stops the plan if they are still not observed. Propose tare before aspirating; \
inspect its baseline result before proposing a separate dosing plan. Zero \
remains a non-idempotent serial write whose `sent_unconfirmed` result proves \
only that its command was sent. A read reports the weight actually observed. \
A cached status reading does not prove a plan measured weight. Balance well `blow_out` requires \
`details.platebalance.geometry.blow_out_enabled: true`, an explicit well \
location at least 2 mm above the measured rim, and a pre-set blow-out flow \
rate no faster than half the pipette's documented dispense default. Never \
use `in_place=true` as a balance workaround. Balance `touch_tip` is unavailable.
- Read current plan records (`current_plans`) to answer whether a proposal was \
approved, run, failed, or aborted. Report only the status and results actually \
recorded. A plan marked `redacted` belongs to another user: you may say it \
exists, not what it measured.
- Summarize balance results from `plate_reports` when asked how a run went \
or for a plate summary or heatmap. Report the stats and name outlying and \
unweighed wells; tell the operator the interactive heatmap opens from the \
plan card's **Plate report** button. Pass a density only if the operator gave \
one; never assume water.

What you cannot do, and must never imply otherwise:
- For work repeated over wells (dispense to each well, weigh each well), \
propose with `for_each_well` — a step template plus the wells — and optional \
`prelude`/`epilogue` steps (tip pickup, tip drop). Never write a plate out as \
hundreds of steps: the gateway expands the pattern and the operator reviews \
the expansion. Put `{{well}}` where the well name goes; single-channel only.
- You cannot run anything. Returning `steps` or `for_each_well` creates a DRAFT \
on the device. A human reviews, approves, and runs it in the panel. Say a newly proposed draft awaits approval. \
For an existing plan, use its current gateway record. `executed` means all \
steps finished; `failed` or `aborted` may include skipped steps, so inspect \
each result before saying what ran. Never infer execution from earlier \
conversation or a cached device status.
- You cannot connect or disconnect the robot, pause or resume a run, or \
reconcile an unknown outcome. Operator-only actions include `startup`, \
`shutdown`, `pause`, `resume`, `stop`, `reconcile`. Everything in `list_actions` \
is yours to propose — an action being an assertion about the physical world \
does not make it operator-only, because approving your draft is how the \
operator makes that assertion. Never tell the operator to perform an action \
by hand when you could propose it.
- You do not decide chemistry. Volumes, reagents, well maps and protocol \
design come from the operator or their project's protocol. If asked to choose \
one, decline and ask what they want.

How to work:
You have NO tools. Everything you may read is attached to the request as JSON: \
`reads.get_status` (incl. deck and consumables; labware definitions are \
reduced to load name, well ordering, parameters and one example well) and \
`reads.get_consumables` in the request; `reads.list_actions` (the action \
catalog with argument schemas) and `reads.get_equipment_docs` (the equipment \
guide: API coverage, naming conventions, limitations) below in these \
instructions; `current_plans` and `plate_reports` in the request. Answer from \
this context; if it lacks what you need, say so.
1. Read the state first. A plan built without looking at the deck is a guess.
2. Check consumables before proposing pipetting — a rack with no fresh tips or \
an unloaded plate will fail at the first step.
Compare `details.mounted_tips` (the gateway's ledger) with \
`details.snapshot.pipettes.<mount>.has_tip` (the robot's own belief) when \
present. If the robot reports a tip the ledger does not, propose `drop_tip` \
with only the pipette before any `pick_up_tip`; it clears the robot's record \
even when the head is bare. If the ledger shows a tip the robot does not (a \
run after a stop starts with none) and the operator says a tip is on the head, \
propose `drop_tip` with `force_drop: true` and only the pipette; never assume it.
3. Use the exact argument names from `list_actions`; unknown keys are \
rejected. Address labware by `labware_nickname`: the observed run nickname or ID \
from status, else the declared deck slot. The setup recipe is not authoritative. Address pipettes by the \
recipe nickname, else the mount ("left" / "right"). `pick_up_tip` may omit \
the rack and position entirely — the gateway picks the next available tip \
from a tracked, size-compatible rack. To discard a tip into the waste, \
{trash_guidance} Address a temperature module by `module` \
(recipe nickname or deck slot). Omit `module` when exactly one temperature \
module is on the deck. `tempmod.set` starts the ramp and returns immediately \
— watch current vs target on the deck; it does not wait. \
`tempmod.deactivate` turns the module off.
For a declared balance plate, address wells by its declared slot (for example \
`"9"`). The compiled run labware may appear as an adapter without wells in \
the deck snapshot; the gateway resolves the wells from the exact declared \
plate definition. Balance dispense clearance is above the highest rim.
4. Propose the smallest plan that does what was asked. Explain each step in one \
short line.
5. If a request is ambiguous, out of scope, or unsafe, say so plainly instead \
of proposing something approximate. Never propose a reset or metadata correction \
just to bypass an interlock. Physical-state corrections require the operator's \
explicit observation; never infer that a tip or rack is fresh.
6. Preserve motion intent: force_direct=true omits the Z retract. Constant-height \
XY motion requires the destination Z to equal the current Z. Never silently \
replace a requested direct path with an arc or invent a clear path.
7. Write `reply` in light Markdown: **bold** for key values and well addresses, \
`backticks` for action and argument names, and a short bullet list for \
enumerations. No headings or tables — the chat window is narrow. \
Return `steps: []` and `for_each_well: null` when proposing nothing. Encode \
every `args_json` / `wells_json` / `overrides_json` as valid JSON object or \
string text.
"""


def system_prompt(docs: Dict[str, Any], device: Device,
                  actions: Optional[Dict[str, Any]] = None) -> str:
    """The instructions plus the device's *static* reads — the equipment
    guide and the action catalog. They change only on a gateway deploy, so
    they live in the system prompt, which is byte-identical from turn to turn
    and user to user for one device and therefore prompt-cached by the
    provider; the per-turn payload carries only live state. The guide's own
    copy of the action catalog (``actions``) is dropped: it is the same
    catalog ``/plans/actions`` serves."""
    model = str(docs.get("model") or "Opentrons OT-2")
    flex = "flex" in model.lower()
    text = SYSTEM_PROMPT.format(
        robot_model=model, equipment_name=device.name,
        trash_guidance=("Flex has no assumed fixed trash: register a physically present bin "
                        "or name an explicit drop well." if flex else
                        "propose drop_tip with only the pipette when its fixed trash is registered."),
    )
    guide = {k: v for k, v in docs.items() if k != "actions"}
    text += ("\n\nreads.get_equipment_docs (the equipment guide, JSON):\n"
             + json.dumps(guide, default=str, separators=(",", ":")))
    if actions is not None:
        text += ("\n\nreads.list_actions (the action catalog with argument schemas, JSON):\n"
                 + json.dumps(actions, default=str, separators=(",", ":")))
    return text


def _history(messages: List[Dict[str, str]]) -> str:
    return "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


@dataclass
class ModelChoice:
    id: str
    backend: str  # "claude-cli" | "openrouter"


def _claude_binary() -> Optional[str]:
    explicit = os.environ.get("ASSISTANT_CLAUDE_BIN")
    if explicit and os.access(explicit, os.X_OK):
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    for candidate in (Path.home() / ".local/bin/claude", Path("/usr/local/bin/claude")):
        if os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _claude_env() -> Dict[str, str]:
    """Keep the host's Claude.ai login; never route through an API key."""
    env = os.environ.copy()
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                 "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        env.pop(name, None)
    return env


_readiness: Dict[str, tuple[float, bool]] = {}


async def _claude_ready(binary: str) -> bool:
    cached = _readiness.get("claude")
    now = time.monotonic()
    if cached and now - cached[0] < _READINESS_TTL_S:
        return cached[1]
    ok = False
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "auth", "status", "--json", stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=_claude_env())
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        status = json.loads(out or b"{}")
        ok = proc.returncode == 0 and status.get("loggedIn") is True
    except (OSError, ValueError, asyncio.TimeoutError):
        ok = False
    _readiness["claude"] = (now, ok)
    return ok


def _openrouter_key() -> Optional[str]:
    key = os.environ.get("ASSISTANT_OPENAI_API_KEY", "").strip()
    return key or None


def _openrouter_base() -> str:
    return os.environ.get("ASSISTANT_OPENAI_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")


async def available_models() -> List[ModelChoice]:
    """What this host can run right now, probed (and cached briefly)."""
    out: List[ModelChoice] = []
    binary = _claude_binary()
    if binary and CLAUDE_MODELS and await _claude_ready(binary):
        out.extend(ModelChoice(m, "claude-cli") for m in CLAUDE_MODELS)
    if _openrouter_key():
        out.extend(ModelChoice(m, "openrouter") for m in OPENROUTER_MODELS)
    return out


class Cancelled(Exception):
    pass


@dataclass
class Turn:
    """One in-flight request: who it belongs to and how to stop it."""
    user: str
    equipment_id: str
    request_id: str
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    process: Optional[asyncio.subprocess.Process] = None

    def stop(self) -> None:
        self.cancel.set()
        proc = self.process
        if proc is not None and proc.returncode is None:
            # The CLI runs in its own session (start_new_session) so the
            # whole tree goes: a surviving child holding our pipes would
            # otherwise keep the wait — and the turn's slot — open.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass


async def run_claude(turn: Turn, model: str, system: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    binary = _claude_binary()
    if binary is None:
        raise RuntimeError("claude CLI is not installed on the dashboard host")
    command = [
        binary, "-p", "--model", model,
        "--output-format", "json", "--json-schema", json.dumps(REPLY_SCHEMA),
        "--system-prompt", system,
        "--tools", "", "--strict-mcp-config", "--safe-mode",
        "--permission-mode", "dontAsk", "--permission-prompts", "none",
        "--disable-slash-commands", "--no-session-persistence",
    ]
    with tempfile.TemporaryDirectory(prefix="lab-assistant-device-") as directory:
        proc = await asyncio.create_subprocess_exec(
            *command, cwd=directory, env=_claude_env(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=10 * 1024 * 1024, start_new_session=True)
        turn.process = proc
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(json.dumps(payload, default=str).encode("utf-8")), timeout=TIMEOUT_S)
        except BaseException as exc:
            # Timeout, cancel, or the client went away: never leave the CLI
            # running. Kill, reap (shielded — we may be inside a cancelled
            # scope), then say which it was.
            turn.stop()
            await asyncio.shield(proc.wait())
            turn.process = None
            if isinstance(exc, asyncio.TimeoutError):
                raise TimeoutError(f"Claude Code did not reply within {TIMEOUT_S:g} s") from exc
            raise
        turn.process = None
    if turn.cancel.is_set():
        raise Cancelled()
    if proc.returncode != 0:
        raise RuntimeError(f"Claude Code exited with status {proc.returncode}: "
                           f"{err.decode('utf-8', 'replace')[-400:].strip()}")
    try:
        envelope = json.loads(out.decode("utf-8", "replace"))
        if envelope.get("is_error"):
            raise ReplyError("Claude Code reported an error")
        structured = envelope.get("structured_output")
        if structured is None:
            structured = json.loads(envelope["result"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ReplyError("Claude Code returned no valid structured reply") from exc
    if not isinstance(structured, dict):
        raise ReplyError("Claude Code returned no structured reply")
    return structured


async def run_openrouter(client: httpx.AsyncClient, turn: Turn, model: str, system: str,
                         payload: Dict[str, Any]) -> Dict[str, Any]:
    key = _openrouter_key()
    if key is None:
        raise RuntimeError("ASSISTANT_OPENAI_API_KEY is not set on the dashboard host")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, default=str)},
        ],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "device_reply", "strict": True, "schema": REPLY_SCHEMA}},
        "provider": {"require_parameters": True},
    }
    try:
        r = await asyncio.wait_for(
            client.post(f"{_openrouter_base()}/chat/completions", json=body, timeout=TIMEOUT_S,
                        headers={"Authorization": f"Bearer {key}"}),
            timeout=TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"{model} did not reply within {TIMEOUT_S:g} s") from exc
    if turn.cancel.is_set():
        raise Cancelled()
    r.raise_for_status()
    data = r.json()
    try:
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ReplyError("the model's reply was cut off; ask for a smaller plan")
        content = choice["message"]["content"]
        structured = json.loads(content) if isinstance(content, str) else content
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ReplyError("the model returned no valid structured reply") from exc
    if not isinstance(structured, dict):
        raise ReplyError("the model returned no structured reply")
    return structured


# ---------------------------------------------------------------------------
# The turn
# ---------------------------------------------------------------------------


async def panel_turn(
    *,
    client: httpx.AsyncClient,
    device: Device,
    headers: Dict[str, str],
    user: str,
    messages: List[Dict[str, str]],
    choice: ModelChoice,
    turn: Turn,
    on_draft: Callable[[Dict[str, Any]], Awaitable[None]] | None = None,
) -> AsyncIterator[Dict[str, Any]]:
    """One turn as the events the gateway bubble already understands:
    ``thinking`` / ``tool_started`` / ``tool_finished`` / ``complete`` /
    ``error``."""
    used: List[str] = []
    yield {"type": "thinking", "round": 1}
    counter = 0

    async def started(path: str) -> None:
        nonlocal counter
        counter += 1
        name = {"/status": "get_status", "/docs/agent": "get_equipment_docs",
                "/plans/actions": "list_actions", "/plans": "list_plans",
                "/plans/plate-report": "get_plate_report"}.get(path, path)
        if name not in used:
            used.append(name)
        yield_queue.append({"type": "tool_started", "id": f"1:read-{counter}", "name": name})
        yield_queue.append({"type": "tool_finished", "id": f"1:read-{counter}", "name": name,
                            "success": True, "error": None})

    yield_queue: List[Dict[str, Any]] = []
    t0 = time.monotonic()
    context = await read_context(client, device, headers, on_read=started)
    for event in yield_queue:
        yield event
    if turn.cancel.is_set():
        raise Cancelled()
    yield {"type": "thinking", "round": 2}
    static = context.pop("static")
    system = system_prompt(static["get_equipment_docs"], device, static["list_actions"])
    payload = {"messages": messages, **context}
    t1 = time.monotonic()
    if choice.backend == "claude-cli":
        structured = await run_claude(turn, choice.id, system, payload)
    else:
        structured = await run_openrouter(client, turn, choice.id, system, payload)
    t2 = time.monotonic()
    if turn.cancel.is_set():
        raise Cancelled()
    reply, body = proposal_from_reply(structured)
    timings = {"reads_s": round(t1 - t0, 2), "model_s": round(t2 - t1, 2),
               "system_chars": len(system), "payload_chars": len(json.dumps(payload, default=str))}
    plan_id: Optional[str] = None
    if body is not None:
        used.append("propose_plan")
        event_id = "2:draft"
        yield {"type": "tool_started", "id": event_id, "name": "propose_plan"}
        error: Optional[str] = None
        try:
            # Not after this point: a cancel cannot undo an accepted draft.
            if turn.cancel.is_set():
                raise Cancelled()
            plan = await create_draft(client, device, headers, body,
                                      created_by=f"assistant ({choice.id}) for {user}")
            plan_id = plan.get("plan_id")
            if on_draft:
                await on_draft(plan)
        except ReplyError as exc:
            error = str(exc)
        yield {"type": "tool_finished", "id": event_id, "name": "propose_plan",
               "success": error is None, "error": error}
        if error:
            reply = f"I could not create that draft: {error}"
        elif not reply:
            reply = "I proposed a draft for your review and approval."
    timings["draft_s"] = round(time.monotonic() - t2, 2)
    logger.info("device chat turn: device=%s model=%s %s", device.equipment_id, choice.id,
                " ".join(f"{k}={v}" for k, v in timings.items()))
    yield {"type": "complete", "result": {
        "reply": reply or "The model returned no reply or draft.",
        "tools_used": used, "plan_id": plan_id, "model": choice.id,
    }}


def make_client() -> httpx.AsyncClient:
    """One client per turn (tests swap the transport)."""
    return httpx.AsyncClient()


def holds_claim(status: Dict[str, Any], user: str) -> bool:
    """Whether ``user`` is the device's current claim holder — directly, or
    through a plan they approved that the gateway runs under its automation
    owner (``automation (approved by <user>)``)."""
    claimed = ((status.get("details") or {}).get("claimed_by")) or {}
    owner = str(claimed.get("owner") or "")
    return owner == user or owner == f"automation (approved by {user})"
