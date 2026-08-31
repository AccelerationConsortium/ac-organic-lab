"""Run executor — turn a bitácora run authorization into a live plan run.

Phase F. The seam between the two apps, and the first code in the dashboard
that executes a *whole plan* rather than a single operator click.

**Why here and not in bitácora** (AGENTIC_ELN_PLAN D-20). Bitácora issues the
authorization; the dashboard runs it. The operator sees one surface either way —
the ELN is already framed at `/workflows` on this origin — so it was never a UX
question, only *which process holds the claim and writes the audit row*. This
app already owns that path end to end: the edge injects a verified
``X-Auth-User``, and ``control.py`` does authorize → claim → act → release →
audit for every operator write. A runner in bitácora would rebuild all of it and
reopen the audit gap the OT-2 panel embed had to close from the device side.

**What crosses the seam** (D-21). A pull, by ``authorization_id``: the runner
asks bitácora at the moment it starts and refuses unless the authorization is
still ``executable``. Deliberately not a push — a pushed package is true as of
when it was sent, and revocation ("that a run was once authorized and then
withdrawn is itself part of the history") only works if the runner asks. The
payload needs no translation: ``package.steps`` are already lab-skills plan
steps, and the pinned ``binding`` says which machine each role is.

**What this module refuses to do.** It does not compile, does not re-plan, does
not substitute, and does not decide readiness. It executes the pinned package
and lets the SDK and the devices adjudicate: ``execute_plan`` re-checks live
``allowed_actions`` and interlocks immediately before every step, which is the
authority — the authorization's stored readiness verdict can be a day old and is
evidence it was sane when approved, never clearance to run now.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from dataclasses import field as dataclass_field
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .custody import (
    CUSTODY_DEVICE_ID,
    CUSTODY_STRICT,
    PLATE_CUSTODY_MISMATCH,
    PLATE_CUSTODY_UNKNOWN,
    PLATE_MOVED,
    Observation,
    custody_recorder,
    observe,
    reconcile,
    record_custody_event,
)
from .lineage import post_transfers, transfers_from
from .record import close_run_record, open_run_record

logger = logging.getLogger("workflow")

#: Where bitácora answers. Loopback by default: both services run on this host,
#: and bitácora binds 127.0.0.1 so the Caddy edge is the only public path in.
BITACORA_URL = os.environ.get("BITACORA_URL", "http://127.0.0.1:8050")

#: Audit `event_type` for a whole-plan run, alongside control.py's
#: `control_action` for a single operator write. A distinct type because the two
#: answer different questions — "who moved the sash" vs "who ran plate 2" — and
#: collapsing them would make the per-action series unreadable.
PLAN_RUN = "plan_run"

#: How the runner authenticates to a device that gates claims on identity.
#:
#: The same credential `control.py` already presents for an operator's single
#: click: the edge-injected `X-Auth-User` plus the shared secret proving the
#: request came through a trusted front. Reused rather than reimplemented —
#: two definitions of "how this app authenticates to a device" is one too many,
#: and the OT-2 gateway aliases `X-Edge-Auth` to its own `X-Edge-Key`
#: specifically so the dashboard's spelling works.
#:
#: The first real run tried an `ac_auth` API key instead and was refused. The
#: key was valid — the sidecar verified it — but the gateway deliberately
#: contacts no external auth service ("so this gate is usable by anyone who
#: deploys the gateway, not only by this lab"), so an issued key means nothing
#: to it. The lesson worth keeping: a credential is only good against the thing
#: that checks it, and which service does the checking is a per-device fact.
#:
#: Consequence for the record: the device stores the **human** in
#: `details.claimed_by.owner` and in its own audit rows, not a machine name.
#: For a long run that means a person's name sits on a claim after they have
#: gone home — more honest than a robot's, and worth knowing.


def device_headers(request: Request) -> dict[str, str]:
    """Identity headers for outbound device calls, or empty when unconfigured.

    Empty is right for a lab whose devices do not gate claims, and fails
    *closed* where they do: the device answers 401 and the run stops at the
    first step having actuated nothing.
    """
    from .control import _device_auth_headers

    return _device_auth_headers(request)

#: Fields of the published package that are digest inputs. `warnings` rides
#: along in the same object but is not covered — it is compiler commentary, not
#: what would run. Kept as an explicit set rather than "everything except
#: warnings" so that a new non-digested field cannot silently join the payload.
_DIGEST_FIELDS = frozenset(
    {"compiler_version", "protocol", "design_ref", "steps", "design",
     "plate_map", "parameters"}
)

#: Digest inputs that bitácora publishes only when present and truthy
#: (`CompiledPackage.digest_payload`: "omit when absent so existing one-plate
#: package digests do not change"). They are NOT required — a one-plate
#: package legitimately lacks `plates` — but when a package carries one it is
#: part of what was authorized and must be hashed. Keeping them in a separate
#: set, rather than adding them to `_DIGEST_FIELDS`, is what lets the
#: missing-input check stay strict for the required set without refusing every
#: one-plate package. `plates` since bitácora template 1.10.0 (PLATES_AS_OBJECTS);
#: `substances` since COMPILER_VERSION 0.5.0 — a package with a substance
#: registry was being refused as a digest mismatch, which reads as tampering
#: and was only drift. `lineage` since 0.6.0 (the compiler-expanded well pairs
#: `app.lineage` files as `transfer` rows), added with the field rather than
#: after the first false tamper report. Every optional field bitácora starts
#: hashing has to be added here, and the failure mode when it is not is a false
#: tamper report.
_OPTIONAL_DIGEST_FIELDS = frozenset({"plates", "substances", "lineage"})


def digest_payload_of(package: dict) -> dict:
    """The exact object bitácora hashed, rebuilt from the published package.

    Mirrors `CompiledPackage.digest_payload` in bitácora: every required field,
    plus each optional field iff it is present **and truthy** — an empty or
    null `plates` is omitted there, so it is omitted here. Shared by the
    verifier and its tests so the two cannot drift.
    """
    payload = {k: v for k, v in package.items() if k in _DIGEST_FIELDS}
    for k in _OPTIONAL_DIGEST_FIELDS:
        if package.get(k):
            payload[k] = package[k]
    return payload


class RunRequest(BaseModel):
    authorization_id: str = Field(min_length=1)
    #: Preflight without touching hardware. `execute_plan` still resolves roles,
    #: re-checks live readiness and evaluates interlocks — it just does not
    #: claim or POST. Useful immediately before a real run, since the
    #: authorization's own verdict may be hours old.
    dry_run: bool = False


@dataclass(frozen=True)
class Authorization:
    """The subset of a bitácora authorization this runner acts on."""

    authorization_id: str
    project_id: str
    protocol_path: str
    commit_sha: str
    package_digest: str
    package: dict
    binding: dict
    authorized_by: str
    executable: bool
    revoked_at: str | None
    expires_at: str
    revoked_by: str | None = None
    #: Nominal plate name → physical Container.hid this run was authorized on
    #: (bitácora's `plate_bindings`). The hids also ride inside the steps
    #: (`custody: {plate, hid, to}`, `{<plate>_hid}` args); this is the summary.
    plate_bindings: dict = dataclass_field(default_factory=dict)

    @property
    def steps(self) -> list[dict]:
        return list(self.package.get("steps") or [])

    @property
    def custody_by_step(self) -> dict[str, dict]:
        """`step_id → {plate, hid, to}` for every step bitácora's compiler
        annotated as completing a handoff (PLATE_TRACKING.md D6). Declared, not
        inferred: only these steps ever write a `move` row."""
        return {
            s["step_id"]: s["custody"] for s in self.steps
            if isinstance(s.get("custody"), dict) and s["custody"].get("hid") and s["custody"].get("to")
        }


class RunRefused(Exception):
    """A gate refused before anything was actuated. The message is the reason."""


async def fetch_authorization(
    client: httpx.AsyncClient, authorization_id: str, *, identity: str | None = None
) -> Authorization:
    """Pull an authorization from bitácora.

    ``identity`` is forwarded as ``X-Auth-User``. Reads do not require it today,
    but the runner passes it anyway: this call is made *on behalf of* the
    operator who pressed Run, and threading identity through from the start is
    much easier than adding it once something depends on its absence.
    """
    headers = {"X-Auth-User": identity} if identity else {}
    try:
        resp = await client.get(
            f"{BITACORA_URL}/authorizations/{authorization_id}",
            headers=headers, timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise RunRefused(f"cannot reach bitácora to read the authorization: {exc}")
    if resp.status_code == 404:
        raise RunRefused(f"no authorization {authorization_id!r}")
    if resp.status_code != 200:
        raise RunRefused(
            f"bitácora returned {resp.status_code} for {authorization_id!r}"
        )
    d = resp.json()
    return Authorization(
        authorization_id=d["authorization_id"],
        project_id=d["project_id"],
        protocol_path=d["protocol_path"],
        commit_sha=d["commit_sha"],
        package_digest=d["package_digest"],
        package=d.get("package") or {},
        binding=d.get("binding") or {},
        plate_bindings=d.get("plate_bindings") or {},
        authorized_by=d["authorized_by"],
        executable=bool(d.get("executable")),
        revoked_at=d.get("revoked_at"),
        expires_at=d["expires_at"],
        revoked_by=d.get("revoked_by"),
    )


def assert_executable(auth: Authorization) -> None:
    """Refuse a revoked or expired authorization, saying which."""
    if auth.revoked_at:
        raise RunRefused(
            f"authorization {auth.authorization_id} was revoked at {auth.revoked_at}"
        )
    if not auth.executable:
        raise RunRefused(
            f"authorization {auth.authorization_id} expired at {auth.expires_at} — "
            "re-authorize, which re-validates the lab (that is the point of the TTL)"
        )


def verify_package_digest(auth: Authorization) -> None:
    """Recompute the digest from the published package and compare.

    This is the only thing that says the package was not edited between being
    authorized and being run, and it is worth doing even though both services
    sit on one host: it costs a hash, and a check that only the issuer can
    perform is not a check.

    Bitácora publishes every digest input inside the package for exactly this
    (`CompiledPackage.digest_payload`). Before that, a verifier had to
    reassemble `protocol` and `design_ref` by splitting filename stems — which
    worked, and coupled two repos through a path convention.
    """
    missing = _DIGEST_FIELDS - set(auth.package)
    if missing:
        raise RunRefused(
            f"the package is missing digest input(s) {sorted(missing)}, so its "
            "digest cannot be verified here — bitácora must publish them "
            "(CompiledPackage.digest_payload)"
        )
    payload = digest_payload_of(auth.package)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    recomputed = "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()
    if recomputed != auth.package_digest:
        raise RunRefused(
            f"package digest mismatch for {auth.authorization_id}: authorized "
            f"{auth.package_digest}, computed {recomputed}. The package is not "
            "what was authorized; nothing was run."
        )


def plan_from(auth: Authorization):
    """Build the SDK `Plan` from the pinned package.

    The one translation in this module, and the one worth watching: a package
    step names its id `step_id`, an SDK `Step` names it `id`. Getting that
    mapping wrong would not fail — it would run the right actions under the
    wrong labels, and the Notes anchored to those labels would be wrong for
    good, since executed `step_id`s are permanent.
    """
    from lab_skills import Plan, Step

    steps = []
    for i, s in enumerate(auth.steps):
        try:
            steps.append(Step(id=s["step_id"], role=s["role"], skill=s["skill"],
                              args=s.get("args") or {}, index=i))
        except KeyError as exc:
            raise RunRefused(
                f"package step {i} is missing {exc.args[0]!r} — it is not a "
                "compiled lab-skills step"
            ) from None
    if not steps:
        raise RunRefused("the authorized package has no steps")
    return Plan(steps=steps)


def notes_from(report, *, authorization_id: str) -> list[dict]:
    """Step-anchored `Note`-shaped records of what happened.

    Not written anywhere yet — the first slice runs without touching the record
    layer (D-23) — but produced in the shape AnaliticaDB takes, so wiring it is
    serialization rather than reverse-engineering. `step_id` is the anchor, which
    is why bitácora's compiler refuses to derive one from a renameable skill name.

    Only non-success steps become notes. A run where everything worked is fully
    described by its `Plan` row; a note per successful step would bury the two
    that matter.
    """
    notes: list[dict] = []
    for s in report.steps:
        if s.status == "succeeded" or s.status == "dry_run":
            continue
        body = s.error or "; ".join(
            v.message for v in (s.violations or []) if getattr(v, "message", None)
        )
        notes.append({
            # `unknown` is its own kind, never folded into deviation: the step
            # was sent and never answered, so the device may have performed it.
            # A note that reads "deviation" invites someone to re-run a step
            # that may already have moved liquid.
            "kind": {"failed": "device_fault", "blocked": "deviation",
                     "skipped": "deviation",
                     "unknown": "outcome_unknown"}.get(s.status, "deviation"),
            "step_id": s.step_id,
            "body": body or f"step {s.step_id} ended as {s.status}",
            "data": {
                "status": s.status,
                "role": s.role,
                "skill": s.skill,
                "equipment_id": s.equipment_id,
                "authorization_id": authorization_id,
            },
        })
    return notes


def plan_row_from(auth: Authorization, report, *, launched_by: str | None = None) -> dict:
    """`Plan`-shaped record of the run (DATABASE_DESIGN §"ELN artifacts").

    A run is a Plan under the campaign's Experiment. `authorization_id` has no
    column of its own, so it rides in `meta` — that thread from "this ran" back
    to "this human approved it, against this commit, with this digest" is the
    whole point of the gate.
    """
    return {
        "project": auth.project_id,
        "protocol_path": auth.protocol_path,
        "source_commit": auth.commit_sha,
        "steps": [
            {"step_id": s.step_id, "action": s.skill,
             "params": {"role": s.role, "equipment_id": s.equipment_id,
                        "status": s.status}}
            for s in report.steps
        ],
        "meta": {
            "authorization_id": auth.authorization_id,
            "package_digest": auth.package_digest,
            # Two humans, deliberately: who approved the run, and who started
            # it. They are different facts and often different people. The
            # device may see only the automation principal, so if these are not
            # recorded here the human vanishes from the trail entirely.
            "authorized_by": auth.authorized_by,
            "launched_by": launched_by,
            "binding": auth.binding,
            # Which physical plates this run was authorized on (D4): the join
            # from this Plan to the custody ledger's Container rows.
            "plate_bindings": auth.plate_bindings,
            "ok": report.ok,
            "dry_run": report.dry_run,
        },
    }


def planned_row_from(auth: Authorization, *, launched_by: str | None, dry_run: bool) -> dict:
    """The same `Plan` shape *before* the run (D9): the pinned steps with status
    `planned`, `ok` unknown. Posted at start so custody `move` rows written
    during the run have a `plan_id` to anchor to; the final per-step statuses
    land in the closing summary Note (a Plan row is never edited)."""
    return {
        "project": auth.project_id,
        "protocol_path": auth.protocol_path,
        "source_commit": auth.commit_sha,
        "steps": [
            {"step_id": s["step_id"], "action": s.get("skill"),
             "params": {"role": s.get("role"), "status": "planned",
                        **({"custody": s["custody"]} if isinstance(s.get("custody"), dict) else {})}}
            for s in auth.steps
        ],
        "meta": {
            "authorization_id": auth.authorization_id,
            "package_digest": auth.package_digest,
            "authorized_by": auth.authorized_by,
            "launched_by": launched_by,
            "binding": auth.binding,
            "plate_bindings": auth.plate_bindings,
            "ok": None,
            "dry_run": dry_run,
        },
    }


def lab_session(request: Request, auth: Authorization):
    """An **un-entered** LabSession over this deployment's registry, bound as the
    authorization pinned it — not as this host happens to be configured now.

    Returns the context manager rather than a live session on purpose:
    `Lab.connect()` gives back a session that is inert until entered, and
    `session.role(...)` raises `LabSession is not active` if it is not. Handing
    an un-entered session to `execute_plan` fails at the *first step*, after
    every gate has passed — which is late, and looked like a device problem when
    it happened here on 2026-08-08. Making the caller write `async with` puts
    the lifetime where it is visible.
    """
    from lab_skills import Lab

    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise RunRefused("no equipment registry is loaded on this server")
    return Lab.connect(
        registry=registry,
        binding=auth.binding or None,
        # Presented on every device call. A device that gates claims on identity
        # refuses the whole run without them, at the first step.
        headers=device_headers(request) or None,
    )


# ── run registry (in-memory) ───────────────────────────────────────────
#
# In-process on purpose, like the aggregator's poll state: one dashboard
# process owns the lab's runs, and a run does not survive an API restart —
# execute_plan's per-step claims die with the process anyway, so pretending a
# persisted row is a live run would be the record overstating reality. The
# durable trail is the plan_run audit rows plus the D-23 AnaliticaDB record
# (record.py); this registry is the *live* view the SSE stream reads from.

@dataclass
class RunState:
    run_id: str
    authorization_id: str
    launched_by: str
    dry_run: bool
    status: str = "running"  # running | finished | refused
    started_at: float = 0.0  # monotonic, for durations
    started_at_utc: str = ""  # wall-clock ISO-8601 — the record layer's Experiment start
    events: list[dict] = dataclass_field(default_factory=list)
    changed: "asyncio.Event" = dataclass_field(default_factory=lambda: asyncio.Event())
    abort_requested: str | None = None  # who asked
    result: dict | None = None
    #: The Plan opened at start (D9): {"opened", "plan_id", "experiment_id"}.
    record: dict = dataclass_field(default_factory=dict)
    #: Notes custody produced mid-run (a mismatch, an unknown outcome), filed
    #: with the run's other notes at close.
    custody_notes: list[dict] = dataclass_field(default_factory=list)
    #: hid → the registry location name this run believes the plate is at right
    #: now, or ``None`` for "we cannot say". Seeded from the run-start ledger
    #: read and advanced only by moves the ledger actually accepted, so the
    #: per-step preflight never holds the ledger to an expectation the ledger
    #: was never told. See :func:`custody_preflight`.
    custody_expected: dict[str, str | None] = dataclass_field(default_factory=dict)

    def emit(self, type_: str, data: dict) -> None:
        self.events.append({"type": type_, "seq": len(self.events), "data": data})
        self.changed.set()
        self.changed = asyncio.Event()


_RUNS: dict[str, RunState] = {}
_RUNS_CAP = 200  # oldest finished runs are dropped past this — a bound, not a policy


def _remember(state: RunState) -> None:
    _RUNS[state.run_id] = state
    if len(_RUNS) > _RUNS_CAP:
        for rid in [r for r, st in _RUNS.items() if st.status != "running"]:
            del _RUNS[rid]
            if len(_RUNS) <= _RUNS_CAP:
                break


async def _drive_run(state: RunState, request: Request, auth: Authorization,
                     plan, connection) -> None:
    """The background task that owns one run, start to finish."""
    identity = state.launched_by

    # One recorder serves both ledger verbs. A package may declare lineage and
    # annotate no custody at all (a plate that never leaves its slot still has
    # wells feeding wells), so it is built for either.
    lineage = list((auth.package or {}).get("lineage") or [])
    # Resolved before `gate` so the closure cannot be called against a
    # half-built run: the preflight below reads all three.
    custody_by_step = auth.custody_by_step
    recorder = custody_recorder() if (custody_by_step or lineage) else None
    locations_cfg = getattr(request.app.state, "locations_config", None)

    async def gate(step) -> str | None:
        # Operator abort — checked first, it is free.
        if state.abort_requested:
            return f"aborted by {state.abort_requested}"
        # D-22: revocation is only real if the runner keeps asking. Between
        # steps, not just at start: an 18 h incubation must stay revocable.
        # fetch failures abort too (the SDK gate fails closed) — a revocation
        # check that cannot run must not quietly stop revoking.
        async with httpx.AsyncClient() as client:
            fresh = await fetch_authorization(
                client, state.authorization_id, identity=identity
            )
        if fresh.revoked_at:
            return (
                f"authorization {state.authorization_id} was revoked at "
                f"{fresh.revoked_at} by {fresh.revoked_by or 'unknown'}"
            )
        if not fresh.executable:
            return f"authorization {state.authorization_id} expired mid-run"
        # Custody preflight (D1, the layer-4 rule PLATE_TRACKING names and
        # nobody had written): is the plate still where this run's own chain of
        # moves says it is? Only for the steps bitácora annotated as handoffs,
        # and only when there is a ledger to ask.
        spec = custody_by_step.get(getattr(step, "id", None))
        if spec is not None and recorder is not None:
            return await custody_preflight(
                state, request, auth, getattr(step, "id", None), spec,
                recorder=recorder, locations=locations_cfg,
            )
        return None

    # D9: open the Plan before the first step so custody rows can anchor to it.
    # A dry run opens nothing (it is a preflight; `write` files it as a draft
    # at the end, as before). A record layer that is down at start leaves
    # `opened: False`, and the close falls back to the end-of-run write.
    if state.dry_run:
        state.record = {"opened": False, "reason": "dry_run"}
    else:
        state.record = await open_run_record(
            plan=planned_row_from(auth, launched_by=identity, dry_run=False),
            design_ref=(auth.package or {}).get("design_ref"),
            operator=identity, started_at=state.started_at_utc,
        )
    state.emit("record", {k: v for k, v in state.record.items()
                          if k in ("opened", "plan_id", "experiment_id", "reason", "error")})

    async def on_step(step_report) -> None:
        state.emit("step", {
            "step_id": step_report.step_id, "status": step_report.status,
            "role": step_report.role, "skill": step_report.skill,
            "equipment_id": step_report.equipment_id, "error": step_report.error,
        })
        spec = custody_by_step.get(step_report.step_id)
        if spec is not None:
            await custody_after_step(state, request, auth, step_report, spec,
                                     recorder=recorder, locations=locations_cfg)

    from lab_skills import execute_plan

    try:
        async with connection as session:
            report = await execute_plan(
                plan, session, owner=identity,
                dry_run=state.dry_run, gate=gate, on_step=on_step,
            )
    except Exception as exc:  # noqa: BLE001 — the task must always conclude
        logger.exception("run %s crashed", state.run_id)
        state.status = "finished"
        state.result = {"ok": False, "error": f"runner crashed: {exc}"}
        state.emit("done", state.result)
        await _record_run_event(request, state.authorization_id, outcome="crashed",
                                owner=identity, detail=str(exc)[:300],
                                duration_s=time.monotonic() - state.started_at)
        return

    duration = time.monotonic() - state.started_at
    state.status = "finished"
    plan_row = plan_row_from(auth, report, launched_by=identity)
    # Custody's own notes (a mismatch, an unknown outcome) file beside the
    # step notes — same Plan, same anchors.
    notes = notes_from(report, authorization_id=auth.authorization_id) + list(state.custody_notes)
    state.result = {
        "authorization_id": auth.authorization_id,
        "ok": report.ok,
        "dry_run": report.dry_run,
        "aborted_reason": report.aborted_reason,
        "duration_s": round(duration, 3),
        "steps": [
            {"step_id": st.step_id, "status": st.status, "role": st.role,
             "skill": st.skill, "equipment_id": st.equipment_id, "error": st.error}
            for st in report.steps
        ],
        # The record layer's shape — produced here and filed just below (D-23).
        "record": {"plan": plan_row, "notes": notes},
    }
    # File the run in AnaliticaDB. Deliberately before `done` is emitted, so a
    # consumer that sees the run finish also sees whether it was recorded — and
    # deliberately incapable of raising, because the run already happened and a
    # failed write must never be reported as a failed run (record.py, property 1).
    state.result["record"]["write"] = await close_run_record(
        opened=state.record, plan=plan_row, notes=notes,
        design_ref=(auth.package or {}).get("design_ref"),
        operator=identity,
        started_at=state.started_at_utc,
        summary={"ok": report.ok, "aborted_reason": report.aborted_reason,
                 "dry_run": report.dry_run, "duration_s": round(duration, 3)},
    )
    # Lineage (D11) files after the Plan is closed and before `done` is emitted,
    # for the same reason the run record does: a consumer that sees the run
    # finish also sees whether its provenance was written. A dry run moved no
    # liquid, so there is nothing to say fed anything.
    if lineage and not state.dry_run:
        state.result["record"]["transfers"] = await lineage_after_run(
            state, auth, report, recorder=recorder)
    state.emit("done", state.result)
    await _record_run_event(
        request, auth.authorization_id,
        outcome=("aborted" if report.aborted_reason else
                 "ok" if report.ok else "failed"),
        owner=identity,
        detail={"steps": len(report.steps), "dry_run": report.dry_run,
                "aborted_reason": report.aborted_reason},
        duration_s=duration,
    )


async def custody_preflight(state: RunState, request: Request, auth: Authorization,
                            step_id: str | None, spec: dict, *,
                            recorder, locations) -> str | None:
    """Before a handoff step runs: is the plate still where this run thinks it
    is? Returns an abort reason, or ``None`` to proceed.

    This is the layer-4 rule ``PLATE_TRACKING.md`` D1 and ``INTERLOCKS.md`` name
    but nobody had written — "plate X must be at location L before step S" —
    with L taken from :attr:`RunState.custody_expected` rather than from the
    protocol. The run-start cross-check (:func:`custody_at_start`) already asks
    the ledger once; between steps is where divergence actually appears, because
    a plate can be lifted off a nest by a human while a 30-minute incubation
    runs, and nothing else would notice until the arm reached for empty air.

    What it compares matters. The expectation is **this run's own chain**: the
    ledger's answer at start, advanced by each move the ledger *accepted*. A
    move the ledger refused (it was down, the container is unknown) sets the
    expectation to ``None`` — unverifiable — so the preflight can never accuse
    the ledger of disagreeing with a move it was never told about. That is the
    same discipline :func:`reconcile` applies to devices: contradiction is a
    finding, absence is not.

    Verdicts, and what each files:

    * ``ok`` — the ledger agrees. One SSE frame, nothing else.
    * ``mismatch`` — the ledger puts the plate somewhere else. Frame, a
      deviation Note naming both places, and a ``plate_custody_mismatch`` row
      with ``payload.phase = "preflight"`` (the same event type the after-step
      contradiction writes; the phase is what tells the two apart).
    * ``not_in_ledger`` — the container has vanished from the ledger mid-run.
      Handled exactly like a mismatch: something happened to a plate this run is
      about to touch, and it is if anything the more alarming of the two.
    * ``unanswered`` — the record layer could not answer. Frame only: a store
      that did not reply has said nothing *about the plate*, and a Note claiming
      a deviation would be the record inventing evidence.

    Under ``CUSTODY_STRICT`` every non-``ok`` verdict aborts the run (including
    ``unanswered`` — strict means custody must be *verifiable*, which is the
    same posture the run-start gate takes). The SDK's gate contract makes that
    an abort with the remaining steps ``skipped``, which is right: a plate that
    is not where the chain requires makes every later step wrong, not just this
    one. A dry run checks and reports but never aborts — a dry run *is* the
    preflight, and refusing to preflight because the preflight failed helps
    nobody.

    Never raises: an internal bug here degrades to an advisory frame. Stopping a
    physical run because the code that watches it is broken is the wrong trade.
    """
    hid, to, plate = spec.get("hid"), spec.get("to"), spec.get("plate")
    frame: dict[str, Any] = {"step_id": step_id, "plate": plate, "hid": hid, "to": to}
    try:
        expected = state.custody_expected.get(hid)
        if expected is None:
            state.emit("custody_preflight",
                       {**frame, "checked": False, "reason": "unverifiable"})
            return None

        cur = await recorder.current_location(
            hid, user=state.launched_by, project=auth.project_id, refresh=True)
        found, actual = cur.get("found"), cur.get("location_name")
        frame.update({"checked": True, "expected": expected, "actual": actual})

        if found is True and actual == expected:
            state.emit("custody_preflight", {**frame, "verdict": "ok"})
            return None

        if found is True:
            verdict = "mismatch"
            body = (f"custody preflight for {step_id}: plate {hid} should be at "
                    f"{expected!r} before this step, but the ledger reads {actual!r}")
        elif found is False:
            verdict = "not_in_ledger"
            body = (f"custody preflight for {step_id}: plate {hid} should be at "
                    f"{expected!r} before this step, but the record layer no "
                    f"longer knows the container")
        else:
            verdict = "unanswered"
            body = (f"custody preflight for {step_id}: plate {hid} should be at "
                    f"{expected!r}; the record layer could not answer "
                    f"({cur.get('error')})")
        frame.update({"verdict": verdict, "error": cur.get("error")})
        state.emit("custody_preflight", frame)

        if verdict != "unanswered":
            state.custody_notes.append({
                "kind": "deviation", "step_id": step_id, "body": body,
                "data": {"custody": spec, "phase": "preflight", "expected": expected,
                         "actual": actual, "found": found,
                         "authorization_id": auth.authorization_id},
            })
            entry = locations.by_name(expected) if locations is not None else None
            await record_custody_event(
                request, PLATE_CUSTODY_MISMATCH,
                device_id=getattr(entry, "equipment", None) or CUSTODY_DEVICE_ID,
                message=body,
                payload={**frame, "phase": "preflight", "run_id": state.run_id,
                         "authorization_id": auth.authorization_id},
            )
        if CUSTODY_STRICT and not state.dry_run:
            return body
        return None
    except Exception as exc:  # noqa: BLE001 — a watcher must not stop the run
        logger.exception("custody preflight failed for %s", step_id)
        state.emit("custody_preflight",
                   {**frame, "checked": False, "reason": f"preflight error: {exc}"})
        return None


def expected_locations(plates: list[dict]) -> dict[str, str | None]:
    """The opening links of the expected-location chain, from
    :func:`custody_at_start`'s ledger read: hid → location name where the ledger
    answered, ``None`` where it did not. Pure.

    ``None`` is load-bearing rather than a gap: a plate the ledger cannot place
    is one the per-step preflight must decline to judge, not one it may assume
    is where the protocol wishes it were."""
    out: dict[str, str | None] = {}
    for p in plates:
        hid = p.get("hid")
        if hid:
            out[hid] = p.get("location_name") if p.get("found") is True else None
    return out


async def custody_after_step(state: RunState, request: Request, auth: Authorization,
                             step_report, spec: dict, *, recorder, locations) -> None:
    """The robot half of custody (PLATE_TRACKING.md D6–D8): a compiled step
    that completes a handoff just ended — record what that means, never raise.

    * ``succeeded`` → one ``move`` row (commanded), then a **fresh** snapshot of
      the device anchoring the destination, ``observe`` / ``reconcile``; a
      contradiction files a deviation Note and a ``plate_custody_mismatch`` row,
      nothing ever auto-corrects.
    * ``unknown`` (sent, never answered) → **no** move row — the last known
      place stands — an ``outcome_unknown`` Note and a ``plate_custody_unknown``
      row, so the gap is visible rather than papered over.
    * ``dry_run`` / ``blocked`` / ``failed`` / ``skipped`` → nothing moved,
      nothing recorded; the SSE frame says why.

    Every lab.db row written here carries ``payload.phase = "after_step"``, the
    counterpart to the ``"preflight"`` :func:`custody_preflight` stamps. Both
    write ``plate_custody_mismatch``, and they mean different things — one is
    "the plate is not where this run left it", the other "the move we just
    commanded did not take" — so the phase is what tells a reader which, without
    resorting to testing for the absence of a key.
    """
    hid, to, plate = spec.get("hid"), spec.get("to"), spec.get("plate")
    frame: dict[str, Any] = {"step_id": step_report.step_id, "plate": plate, "hid": hid, "to": to}
    try:
        status = step_report.status
        if status in ("dry_run", "blocked", "failed", "skipped"):
            state.emit("custody", {**frame, "recorded": False, "reason": status})
            return
        entry = locations.by_name(to) if locations is not None else None
        device = getattr(entry, "equipment", None) or "custody"
        plan_id = state.record.get("plan_id")
        if status == "unknown":
            # The plate may or may not have arrived, so the chain can no longer
            # say where it is. Later steps preflight as unverifiable rather than
            # against a place nobody can vouch for.
            state.custody_expected[hid] = None
            body = (f"step {step_report.step_id} was sent and never answered: plate {hid} "
                    f"may or may not have arrived at {to}; its last recorded place stands")
            state.custody_notes.append({
                "kind": "outcome_unknown", "step_id": step_report.step_id, "body": body,
                "data": {"custody": spec, "status": status, "authorization_id": auth.authorization_id},
            })
            await record_custody_event(request, PLATE_CUSTODY_UNKNOWN, device_id=device,
                                       message=body, payload={**frame, "phase": "after_step",
                                                              "run_id": state.run_id,
                                                              "authorization_id": auth.authorization_id})
            state.emit("custody", {**frame, "recorded": False, "reason": "unknown"})
            return
        # succeeded — 1. the commanded move
        if recorder is None:
            result = {"recorded": False, "reason": "not_configured"}
        else:
            result = await recorder.record_move(
                hid=hid, to=to, performed_by=step_report.equipment_id or step_report.role,
                recorder=state.launched_by, project=auth.project_id,
                plan_id=plan_id, step_id=step_report.step_id,
                params={"authorization_id": auth.authorization_id, "run_id": state.run_id,
                        "role": step_report.role, "skill": step_report.skill,
                        "plate": plate, "via": "executor"},
            )
        # Advance the chain the preflight compares against — but only as far as
        # the ledger actually went. A move it refused leaves the plate somewhere
        # the ledger does not know, which is exactly "we cannot say".
        state.custody_expected[hid] = to if result.get("recorded") else None
        # 2. the observed side — a fresh read of the destination's device
        observation = Observation("none", None, "no registry entry")
        aggregator = getattr(request.app.state, "aggregator", None)
        if entry is not None and entry.equipment and aggregator is not None:
            try:
                snapshot = await aggregator.fetch_one(entry.equipment)
            except Exception as exc:  # noqa: BLE001 — a failed read is "unobservable"
                snapshot = None
                logger.warning("custody: could not read %s after %s: %s", entry.equipment, step_report.step_id, exc)
            observation = observe(snapshot, entry, locations)
        verdict = reconcile(hid, observation)
        frame.update({"recorded": bool(result.get("recorded")), "result": result,
                      "observed": observation.as_dict(), "verdict": verdict})
        state.emit("custody", frame)
        await record_custody_event(
            request, PLATE_MOVED, device_id=device,
            message=f"{hid} → {to} ({step_report.step_id}) → {'recorded' if result.get('recorded') else result.get('reason')}; {verdict}",
            payload={**frame, "phase": "after_step", "run_id": state.run_id,
                     "authorization_id": auth.authorization_id,
                     "performed_by": step_report.equipment_id or step_report.role,
                     "source": "executor"},
        )
        if verdict == "mismatch":
            body = (f"custody mismatch after {step_report.step_id}: plate {hid} was recorded at {to} "
                    f"but {observation.source} reads {observation.value!r}")
            state.custody_notes.append({
                "kind": "deviation", "step_id": step_report.step_id, "body": body,
                "data": {"custody": spec, "observed": observation.as_dict(),
                         "authorization_id": auth.authorization_id},
            })
            await record_custody_event(request, PLATE_CUSTODY_MISMATCH, device_id=device,
                                       message=body, payload={**frame, "phase": "after_step",
                                                              "run_id": state.run_id,
                                                              "authorization_id": auth.authorization_id})
    except Exception as exc:  # noqa: BLE001 — custody must never stop a run
        logger.exception("custody hook failed for %s", step_report.step_id)
        state.emit("custody", {**frame, "recorded": False, "reason": f"hook error: {exc}"})


async def lineage_after_run(state: RunState, auth: Authorization, report, *,
                            recorder) -> dict[str, Any]:
    """File one ``transfer`` row per declared well pair the run completed
    (PLATE_TRACKING.md D11). Returns a summary; **never raises**.

    Custody records where a plate went, step by step; this records what fed
    what, once, at the end. The timing differs because the facts differ: a move
    is only true at the instant it happens, while "A1 fed A1" is settled by the
    step's outcome and nothing later revises it — so deriving from the finished
    report costs nothing and keeps 96 ledger writes out of the run's critical
    path.

    Which pairs ran is decided in :func:`~app.lineage.transfers_from` from the
    package's compiler-expanded ``lineage`` and the final per-step statuses. The
    equipment lookup goes through the *report*, not the package: a compiled step
    knows its role, and only the run knows which machine that role resolved to.

    The whole thing is wrapped because the run has already physically happened
    (``record.py`` property 1). A provenance write that fails must read as
    exactly that — not as a failed run.
    """
    if recorder is None:
        return {"emitted": 0, "reason": "not_configured"}
    try:
        specs = transfers_from(auth.package or {},
                               {s.step_id: s.status for s in report.steps})
        equipment = {s.step_id: s.equipment_id for s in report.steps}
        summary = await post_transfers(
            recorder, specs,
            plan_id=state.record.get("plan_id"),
            operator=state.launched_by,
            project=auth.project_id,
            run_id=state.run_id,
            authorization_id=auth.authorization_id,
            performed_by_lookup=equipment.get,
        )
        return {"derived": len(specs), **summary}
    except Exception as exc:  # noqa: BLE001 — the run already happened
        logger.exception("lineage transfers failed for run %s", state.run_id)
        return {"emitted": 0, "error": str(exc)[:300]}


async def custody_at_start(auth: Authorization, *, identity: str) -> tuple[list[dict], list[str]]:
    """Run-start cross-check (D7): where the record layer says each bound plate
    is now, and warnings for any it does not know. Returns ``(plates, warnings)``.
    Under ``CUSTODY_STRICT=1`` the caller refuses on warnings."""
    recorder = custody_recorder() if auth.plate_bindings else None
    plates: list[dict] = []
    warnings: list[str] = []
    if recorder is None:
        return plates, warnings
    for plate, hid in auth.plate_bindings.items():
        cur = await recorder.current_location(hid, user=identity, project=auth.project_id)
        plates.append({"plate": plate, **cur})
        if cur.get("found") is False:
            warnings.append(f"plate '{plate}' is bound to {hid!r}, which the record layer does not know")
        elif cur.get("found") is None:
            warnings.append(f"plate '{plate}' ({hid}): record layer could not answer — {cur.get('error')}")
    return plates, warnings


def build_workflow_router() -> APIRouter:
    router = APIRouter(prefix="/api/workflow", tags=["workflow"])

    @router.post("/runs", status_code=202)
    async def start_run(body: RunRequest, request: Request) -> dict:
        """Start an authorized run in the background; progress is on the SSE
        stream. Returns as soon as the gates pass — a 141 s transfer already
        outlived a synchronous POST, and an 18 h incubation makes one absurd.

        The gates still run inline, so a refusal is still a 409 with the
        reason, not a run_id that dies immediately: nothing may be accepted
        for execution that was not verified first.
        """
        identity = request.headers.get("X-Auth-User") or "ac-organic-lab-dashboard"

        async with httpx.AsyncClient() as client:
            try:
                auth = await fetch_authorization(
                    client, body.authorization_id, identity=identity
                )
                assert_executable(auth)
                verify_package_digest(auth)
                plan = plan_from(auth)
                connection = lab_session(request, auth)
            except RunRefused as exc:
                await _record_run_event(
                    request, body.authorization_id, outcome="refused",
                    owner=identity, detail=str(exc),
                )
                raise HTTPException(status_code=409, detail=str(exc)) from None

        # Where the bound plates are *now*, per the record layer — a warning on
        # the `started` frame, or a refusal under CUSTODY_STRICT (D7).
        plates, custody_warnings = await custody_at_start(auth, identity=identity)
        if custody_warnings and CUSTODY_STRICT and not body.dry_run:
            reason = "custody check failed: " + "; ".join(custody_warnings)
            await _record_run_event(request, body.authorization_id, outcome="refused",
                                    owner=identity, detail=reason)
            raise HTTPException(status_code=409, detail=reason)

        state = RunState(
            run_id=f"run_{uuid.uuid4().hex[:12]}",
            authorization_id=auth.authorization_id,
            launched_by=identity,
            dry_run=body.dry_run,
            started_at=time.monotonic(),
            started_at_utc=datetime.now(timezone.utc).isoformat(),
            custody_expected=expected_locations(plates),
        )
        _remember(state)
        state.emit("started", {
            "authorization_id": auth.authorization_id,
            "protocol_path": auth.protocol_path,
            "steps_total": len(plan.steps),
            "dry_run": body.dry_run,
            "launched_by": identity,
            "plate_bindings": auth.plate_bindings,
            "custody": plates,
            "custody_warnings": custody_warnings,
            "custody_steps": sorted(auth.custody_by_step),
        })
        asyncio.get_running_loop().create_task(
            _drive_run(state, request, auth, plan, connection)
        )
        return {"run_id": state.run_id, "status": state.status,
                "authorization_id": auth.authorization_id}

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict:
        state = _RUNS.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
        return {"run_id": run_id, "status": state.status,
                "authorization_id": state.authorization_id,
                "launched_by": state.launched_by, "dry_run": state.dry_run,
                "abort_requested": state.abort_requested,
                "events": len(state.events), "result": state.result}

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: str) -> StreamingResponse:
        """SSE stream of run events, replaying from the start.

        Replay-then-follow so a client that connects late (or reconnects) sees
        the whole run, not a tail: events carry `seq`, and the stream is the
        same list `get_run` counts. Ends after the `done` event.
        """
        state = _RUNS.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id!r}")

        async def _stream():
            i = 0
            while True:
                while i < len(state.events):
                    ev = state.events[i]
                    yield f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
                    if ev["type"] == "done":
                        return
                    i += 1
                changed = state.changed
                if state.status != "running" and i >= len(state.events):
                    return
                try:
                    await asyncio.wait_for(changed.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream")

    @router.post("/runs/{run_id}/abort")
    async def abort_run(run_id: str, request: Request) -> dict:
        """Cooperative abort: takes effect at the next step boundary.

        Cooperative because mid-step is the device's territory — yanking a
        claim out from under a seal cycle is how a plate gets stuck in a hot
        chamber. The current step finishes (or times out); everything after is
        skipped with the reason on the report.
        """
        state = _RUNS.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
        who = request.headers.get("X-Auth-User") or "unknown"
        if state.status != "running":
            return {"run_id": run_id, "status": state.status,
                    "detail": "run already finished; nothing to abort"}
        if not state.abort_requested:
            state.abort_requested = who
            state.emit("abort_requested", {"by": who})
            await _record_run_event(request, state.authorization_id,
                                    outcome="abort_requested", owner=who)
        return {"run_id": run_id, "status": state.status,
                "abort_requested": state.abort_requested}

    return router


async def _record_run_event(
    request: Request,
    authorization_id: str,
    *,
    outcome: str,
    owner: str,
    detail: Any = None,
    duration_s: float | None = None,
) -> None:
    """One audit row per run attempt, in the same series as operator control.

    Best-effort and swallowed on failure, exactly like ``control.py``'s: an
    audit write must never be the reason a run fails. Per-step rows are the
    device exporters' job — they see the actual command; this row is the run.
    """
    db = getattr(request.app.state, "db", None)
    if db is None:
        return
    payload: dict[str, Any] = {"authorization_id": authorization_id,
                               "outcome": outcome, "owner": owner}
    if duration_s is not None:
        payload["duration_s"] = round(duration_s, 3)
    if detail is not None:
        payload["detail"] = detail
    try:
        import asyncio
        import functools

        await asyncio.get_event_loop().run_in_executor(
            None,
            functools.partial(
                db.record_equipment_event,
                "workflow",
                PLAN_RUN,
                message=f"{owner} ran {authorization_id} → {outcome}",
                payload=payload,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — auditing must not break a run
        logger.warning("audit write failed for run %s: %s", authorization_id, exc)
