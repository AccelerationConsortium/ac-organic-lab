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

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

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

    @property
    def steps(self) -> list[dict]:
        return list(self.package.get("steps") or [])


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
        authorized_by=d["authorized_by"],
        executable=bool(d.get("executable")),
        revoked_at=d.get("revoked_at"),
        expires_at=d["expires_at"],
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
    payload = {k: v for k, v in auth.package.items() if k in _DIGEST_FIELDS}
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
            "kind": {"failed": "device_fault", "blocked": "deviation",
                     "skipped": "deviation"}.get(s.status, "deviation"),
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
            "ok": report.ok,
            "dry_run": report.dry_run,
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


def build_workflow_router() -> APIRouter:
    router = APIRouter(prefix="/api/workflow", tags=["workflow"])

    @router.post("/runs")
    async def start_run(body: RunRequest, request: Request) -> dict:
        """Execute an authorized package. Actuates hardware unless ``dry_run``.

        Sequential and synchronous for this first slice: it returns when the run
        finishes. That is honest for a 14-step transfer and wrong for an 18 h
        incubation — the background-run + SSE stream is the next slice, and the
        return shape here is what it will stream.
        """
        identity = request.headers.get("X-Auth-User")
        started = time.monotonic()

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
                # Refused before any device was touched. Audited anyway: an
                # attempt to run a revoked or tampered package is exactly the
                # thing a record should show.
                await _record_run_event(
                    request, body.authorization_id, outcome="refused",
                    owner=identity or "unknown", detail=str(exc),
                )
                raise HTTPException(status_code=409, detail=str(exc)) from None

        from lab_skills import execute_plan

        # The session must be *entered*: outside this block `session.role(...)`
        # raises and every step fails, after the gates have already passed.
        async with connection as session:
            report = await execute_plan(
                plan, session,
                owner=identity or "ac-organic-lab-dashboard",
                dry_run=body.dry_run,
            )
        duration = time.monotonic() - started

        await _record_run_event(
            request, auth.authorization_id,
            outcome="ok" if report.ok else "failed",
            owner=identity or "ac-organic-lab-dashboard",
            detail={"steps": len(report.steps), "dry_run": report.dry_run},
            duration_s=duration,
        )
        return {
            "authorization_id": auth.authorization_id,
            "ok": report.ok,
            "dry_run": report.dry_run,
            "duration_s": round(duration, 3),
            "steps": [
                {"step_id": s.step_id, "status": s.status, "role": s.role,
                 "skill": s.skill, "equipment_id": s.equipment_id,
                 "error": s.error}
                for s in report.steps
            ],
            # The record layer's shape, produced but not written (D-23).
            "record": {"plan": plan_row_from(auth, report, launched_by=identity),
                       "notes": notes_from(report, authorization_id=auth.authorization_id)},
        }

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
