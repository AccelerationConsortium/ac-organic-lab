"""Server-side execution of approved assistant plans (UI_DESIGN §5.3b).

Until 2026-10-15 the browser that approved a Control-mode plan also ran it,
one ``/api/equipment/{id}/control/{action}`` POST per step, so closing the tab
stopped the plan between steps and every read result lived only in that tab.
This module runs the same steps from the dashboard server instead, on the
model of the OT-2 gateway's ``PlanExecutor`` (``opentrons-server``
``gateway/plans.py``): **a human approves and starts; the server executes.**

The rules, all enforced here and none delegated to the browser:

1. **Only a human starts a plan.** ``POST /api/assistant/plans/{id}/execute``
   needs the approval recorded by the engine's approve route (the hash of
   exactly the steps the card showed, ``lab_assistant.engine``), sent back
   again; the caller must be the approver; their ac_auth *browser session* is
   re-verified with the sidecar, cookie only, so a machine principal cannot
   start one; and their per-equipment authorization is checked. Nothing in
   the lab-control MCP server can reach this route: the model proposes, it
   never approves or executes.
2. **The plan holds the device.** Execute takes a fresh device claim as the
   approver (refused 409/423 when anyone else holds the device), under session
   ``assistant-plan-<id>``, and the run keeps it alive with heartbeats until it
   ends — the counterpart of ``ClaimManager.hand_to_plan``. The device-side
   owner stays the approver's verified identity, because the device binds a
   claim to the identity the edge vouched for; the run record names the holder
   ``automation (approved by …)``. While it runs, nobody else (the approver's
   own tile clicks included) can take control.
3. **Every step takes the ordinary control path.** Each step goes through
   ``control._proxy`` with the plan's claim token: per-equipment authz per step,
   the device's own 412/423, the per-kind timeouts, and the ``control_action``
   audit row stamped ``origin: assistant-plan``, ``plan: <id>#<step>``. Before
   each step the device's live ``allowed_actions`` are re-read; a step the
   device does not offer right now halts the plan.
4. **Fail fast and record first.** The first refused or failed step halts the
   plan and marks the rest ``skipped``. Each step's start is saved before it is
   sent and its outcome (with a ``read.*`` step's full response) before the next
   one starts; a save that fails stops the plan, so nothing runs unrecorded.
5. **Anyone signed in may pause, stop or abort; only the approver resumes.**
   None of them interrupts a step already sent — a synchronous device call
   completes and keeps its real outcome. Stopping hardware mid-motion is the
   device's own safety-floor stop, which stays an operator button.
6. **Approvals are memory; runs are not.** An approval dies with the process,
   as on the OT-2 (an id minted before a restart must not start after it). The
   *run record* is durable SQLite. A run that was in progress when the process
   stopped is marked ``interrupted`` on start-up and its in-flight step
   ``unknown`` — software recovery cannot establish whether a physical action
   happened, so nothing is resumed or repeated.
7. **Results reach the notebook.** When the approver names an ELN project, the
   finished (or interrupted) run is journaled in ``plan_results`` and filed in
   BitacoraDB as an UNFORMATTED Experiment by its retry loop, with the
   approver's own standing — the same path OT-2 gateway plans take.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.requests import Request as StarletteRequest

from lab_assistant.plan_contract import plan_step_hash

from . import control

logger = logging.getLogger(__name__)

ORIGIN = "assistant-plan"
#: Terminal run states; ``interrupted`` is set only by start-up recovery.
TERMINAL = frozenset({"executed", "failed", "aborted", "interrupted"})
ACTIVE = frozenset({"executing", "paused"})
#: Polled while paused, so stop/abort/resume take effect promptly.
_PAUSE_POLL_S = 0.25


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def claim_session_id(plan_id: str) -> str:
    """The claim session a running plan holds — what the device shows in
    ``details.claimed_by.session_id``."""
    return f"assistant-plan-{plan_id}"


def keeps_response(action: str) -> bool:
    """A ``read.*`` step's response is the measurement; keep it in full."""
    return action.startswith("read.")


# ---------------------------------------------------------------------------
# Durable run records
# ---------------------------------------------------------------------------


class PlanRunStore:
    """One row per run: the whole record as JSON, rewritten at every step."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS plan_runs ("
                " plan_id TEXT PRIMARY KEY, equipment_id TEXT NOT NULL,"
                " approved_by TEXT NOT NULL, status TEXT NOT NULL,"
                " started_at TEXT NOT NULL, finished_at TEXT, record TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS plan_runs_started ON plan_runs (started_at)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, run: dict[str, Any]) -> None:
        payload = json.dumps(run, default=str)
        with self._lock, self.connect() as db:
            db.execute(
                "INSERT INTO plan_runs VALUES (?,?,?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET"
                " status=excluded.status, finished_at=excluded.finished_at, record=excluded.record",
                (run["plan_id"], run["equipment_id"], run["approved_by"], run["status"],
                 run["started_at"], run.get("finished_at"), payload))

    def get(self, plan_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as db:
            row = db.execute("SELECT record FROM plan_runs WHERE plan_id=?", (plan_id,)).fetchone()
        return json.loads(row["record"]) if row else None

    def list(self, *, limit: int = 50, equipment_id: str | None = None,
             active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT record FROM plan_runs"
        where, args = [], []
        if equipment_id:
            where.append("equipment_id=?")
            args.append(equipment_id)
        if active_only:
            where.append("status IN ('executing','paused')")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY started_at DESC LIMIT ?"
        with self.connect() as db:
            rows = db.execute(sql, (*args, limit)).fetchall()
        return [json.loads(r["record"]) for r in rows]

    def recover_interrupted(self) -> list[dict[str, Any]]:
        """Mark every run left in progress by a previous process as
        ``interrupted``. A step that had started but not finished is
        ``unknown`` — it may or may not have happened on the device."""
        recovered = []
        for run in self.list(limit=10_000, active_only=True):
            for result in run["results"]:
                if result["outcome"] == "running":
                    result["outcome"] = "unknown"
                    result["message"] = (
                        "The dashboard stopped while this step was in flight; whether it "
                        "happened on the device is unknown. Check the device before repeating it.")
                elif result["outcome"] == "pending":
                    result["outcome"] = "skipped"
            run["status"] = "interrupted"
            run["halt_reason"] = "the dashboard server stopped while the plan was running"
            run["finished_at"] = _now()
            run["paused"] = False
            self.save(run)
            recovered.append(run)
        return recovered


# ---------------------------------------------------------------------------
# Seams (monkeypatched in tests)
# ---------------------------------------------------------------------------


async def _verify_human_session(client: httpx.AsyncClient, cookie: str) -> str | None:
    """The user the ac_auth sidecar resolves this session cookie to, or None.

    Cookie only — never ``X-Api-Key`` — so only an interactive browser
    session can start a plan (the same rule as the SSH console)."""
    resp = await client.get(f"{control._authz_base()}/auth/verify",
                            headers={"cookie": cookie}, timeout=10)
    if resp.status_code != 200:
        return None
    return resp.headers.get("x-auth-user") or None


async def _read_live_status(app: Any, equipment_id: str) -> Any:
    """Fresh ``/status`` through the SDK (not the poll cache): the step about
    to be sent is checked against what the device says *now*."""
    from .assistant_control import _read_status

    return await _read_status(app.state.aggregator.registry, equipment_id)


def _step_startable(entry: Any, status: Any, action: str) -> bool:
    from .assistant_control import _action_startable

    return _action_startable(entry, status, action)


async def _project_member(client: httpx.AsyncClient, user: str, project: str) -> bool:
    from .plan_results import approver_is_member

    return await approver_is_member(client, user, project)


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


class PlanExecuteRequest(BaseModel):
    """``POST /api/assistant/plans/{id}/execute``."""

    step_hash: str = Field(min_length=16, max_length=128)
    #: ELN project to file the results under; None keeps them on the dashboard.
    eln_project: str | None = Field(default=None, min_length=1, max_length=200)


@dataclass
class _Live:
    """What only the running process knows about a run: never persisted."""

    run: dict[str, Any]
    entry: Any
    headers: dict[str, str]  # the approver's identity headers + session cookie
    token: str
    edge_headers: dict[str, str]
    heartbeat_s: float
    paused: bool = False
    abort_reason: str | None = None
    claim_lost: str | None = None
    task: asyncio.Task | None = None
    heartbeat: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _plan_request(app: Any, headers: dict[str, str], *, plan_ref: str | None = None,
                  token: str | None = None) -> StarletteRequest:
    """A request object for ``control``'s helpers, built from the approver's
    identity captured at execute time. Those helpers read the identity, the
    audit origin/plan stamp, the caller's claim token and ``app.state`` from
    it — exactly what the browser's per-step POST used to carry."""
    h = {**headers, "x-control-origin": ORIGIN}
    if plan_ref:
        h["x-control-plan"] = plan_ref
    if token:
        h["x-claim-token"] = token
    return StarletteRequest({
        "type": "http", "method": "POST", "path": "/internal/assistant-plan",
        "raw_path": b"/internal/assistant-plan", "query_string": b"", "app": app,
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in h.items()],
    })


def _summary(run: dict[str, Any]) -> dict[str, Any]:
    results = run["results"]
    return {k: run.get(k) for k in (
        "plan_id", "equipment_id", "equipment_name", "kind", "status", "paused", "approved_by",
        "owner", "started_at", "finished_at", "halt_reason", "eln_project", "notebook")} | {
        "steps_total": len(results),
        "steps_ok": sum(r["outcome"] == "ok" for r in results),
        "current_step": next((r["index"] for r in results if r["outcome"] == "running"), None),
    }


def _public(run: dict[str, Any], *, user: str, role: str | None) -> dict[str, Any]:
    """The run as one caller may see it. Anyone signed in sees its progress
    (they may need to stop it); the measurements go to the approver and admins."""
    out = json.loads(json.dumps(run, default=str))
    out.update(_summary(run))
    if user != run["approved_by"] and role != "admin":
        for result in out["results"]:
            if "response" in result:
                result["response"] = None
                result["response_withheld"] = True
    return out


class PlanRunner:
    def __init__(self, app: Any, plans: dict[str, dict[str, Any]], store: PlanRunStore):
        self.app = app
        self.plans = plans
        self.store = store
        self.live: dict[str, _Live] = {}

    # -- helpers ------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return control._get_control_client(_plan_request(self.app, {}))

    async def _audit(self, equipment_id: str, event: str, message: str,
                     payload: dict[str, Any]) -> None:
        from lab_assistant.engine import _audit

        try:
            await _audit(getattr(self.app.state, "db", None), equipment_id, event, message, payload)
        except Exception as exc:  # noqa: BLE001 - auditing must never break a run
            logger.warning("plan audit %s failed for %s: %s", event, equipment_id, exc)

    def active_on(self, equipment_id: str) -> _Live | None:
        return next((lv for lv in self.live.values()
                     if lv.run["equipment_id"] == equipment_id and lv.run["status"] in ACTIVE), None)

    async def _save(self, run: dict[str, Any]) -> str | None:
        try:
            # Snapshot on the loop thread: the loop keeps mutating ``run``
            # while the worker thread writes.
            snapshot = json.loads(json.dumps(run, default=str))
            await asyncio.to_thread(self.store.save, snapshot)
            return None
        except Exception as exc:  # surfaced as the halt reason
            logger.exception("plan %s: run record not saved", run["plan_id"])
            return f"run record not saved: {exc}"

    # -- start ----------------------------------------------------------------

    async def execute(self, plan_id: str, request: Request, body: PlanExecuteRequest) -> dict[str, Any]:
        actor = request.headers.get("x-auth-user")
        if not actor:
            raise HTTPException(401, "Sign in to run a plan.")
        now = time.monotonic()
        for pid in [p for p, r in self.plans.items() if r["expires_at"] <= now and not r.get("starting")]:
            self.plans.pop(pid, None)
        rec = self.plans.get(plan_id)
        if rec is None:
            if self.store.get(plan_id) is not None:
                raise HTTPException(409, "this plan has already been run; its approval was spent")
            raise HTTPException(404, "unknown plan — it expired, was finished, or the dashboard "
                                     "restarted since it was approved; ask again for a fresh one")
        if rec.get("starting"):
            raise HTTPException(409, "this plan is already being started")
        if rec.get("approved_at") is None:
            raise HTTPException(409, "this plan has not been approved")
        if rec.get("approved_by", rec.get("actor")) != actor:
            raise HTTPException(403, "only the operator who approved this plan may start it")
        plan = rec["plan"]
        steps = plan.get("steps") or []
        if not steps or body.step_hash != plan["step_hash"] or plan_step_hash(steps) != plan["step_hash"]:
            raise HTTPException(409, "the plan changed since it was approved — review it again")
        aggregator = getattr(self.app.state, "aggregator", None)
        entry = aggregator.entry(plan["equipment_id"]) if aggregator is not None else None
        if entry is None:
            raise HTTPException(404, f"unknown equipment {plan['equipment_id']!r}")
        if getattr(entry, "protocol", None) in (None, "1.0"):
            raise HTTPException(409, {
                "code": "no_claim_protocol",
                "message": (f"{entry.id} has no claim protocol (STATUS_SPEC v1.0), so the server "
                            "cannot hold it for the plan; run this plan from the browser"),
            })
        if self.active_on(entry.id) is not None:
            raise HTTPException(409, f"another assistant plan is already running on {entry.id}")

        # Reserve before the first await: two clicks cannot both spend it.
        rec["starting"] = True
        try:
            return await self._start(plan_id, rec, plan, entry, actor, request, body)
        finally:
            rec.pop("starting", None)

    async def _start(self, plan_id: str, rec: dict[str, Any], plan: dict[str, Any], entry: Any,
                     actor: str, request: Request, body: PlanExecuteRequest) -> dict[str, Any]:
        client = self._client()
        cookie_value = request.cookies.get(control._AUTH_COOKIE_NAME)
        if control._authz_enforced():
            if not cookie_value:
                raise HTTPException(401, "Starting a plan needs a signed-in browser session.")
            try:
                session_user = await _verify_human_session(
                    client, f"{control._AUTH_COOKIE_NAME}={cookie_value}")
            except httpx.HTTPError as exc:
                raise HTTPException(503, f"Could not verify your session; not started: {exc}") from exc
            if session_user != actor:
                raise HTTPException(401, "Your browser session could not be verified; sign in again.")
        eln_project = body.eln_project.strip() if body.eln_project else None
        if eln_project:
            try:
                member = await _project_member(client, actor, eln_project)
            except (httpx.HTTPError, ValueError) as exc:
                raise HTTPException(503, f"Could not check your ELN projects: {exc}") from exc
            if not member:
                raise HTTPException(403, f"{actor} is not a member of ELN project {eln_project!r}")

        headers = {"x-auth-user": actor}
        if request.headers.get("x-auth-role"):
            headers["x-auth-role"] = request.headers["x-auth-role"]
        if cookie_value:
            headers["cookie"] = f"{control._AUTH_COOKIE_NAME}={cookie_value}"
        probe = _plan_request(self.app, headers, plan_ref=f"{plan_id}#0")
        # Per-equipment authorization (audits its own refusals), then the
        # fresh claim: refused while anyone else holds the device.
        await control._authorize_control(probe, client, entry.id, "claim", "POST", actor)
        candidates = control._device_auth_candidates(probe, entry)
        token, heartbeat_s, edge_headers = await control._acquire_claim_with_fallback(
            client, entry.base_url, entry.status_path, entry.id, actor, candidates,
            session_id=claim_session_id(plan_id))

        steps = plan["steps"]
        run = {
            "plan_id": plan_id,
            "equipment_id": entry.id,
            "equipment_name": plan.get("equipment_name") or entry.id,
            "kind": plan.get("kind") or getattr(entry, "kind", None),
            "step_hash": plan["step_hash"],
            "reason": plan.get("reason"),
            "proposed_to": rec.get("actor"),
            "approved_by": actor,
            "owner": f"automation (approved by {actor})",
            "claim_session": claim_session_id(plan_id),
            "eln_project": eln_project,
            "status": "executing",
            "paused": False,
            "started_at": _now(),
            "finished_at": None,
            "halt_reason": None,
            "notebook": None,
            "steps": [{"action": s["action"], "passthrough_action": s["passthrough_action"],
                       "args": s.get("args") or {}} for s in steps],
            "results": [{"index": i + 1, "action": s["action"], "outcome": "pending",
                         "status_code": None, "message": None, "started_at": None,
                         "finished_at": None} for i, s in enumerate(steps)],
        }
        error = await self._save(run)
        if error:
            await control._release_claim_best_effort(
                client, entry.base_url, entry.status_path, token, entry.id, extra_headers=edge_headers)
            raise HTTPException(503, f"{error}; the plan was not started")
        # The approval is spent: from here the run record is the plan.
        self.plans.pop(plan_id, None)
        live = _Live(run=run, entry=entry, headers=headers, token=token,
                     edge_headers=edge_headers, heartbeat_s=heartbeat_s)
        self.live[plan_id] = live
        live.heartbeat = asyncio.create_task(self._keep_claim(live), name=f"plan-claim-{plan_id}")
        await self._audit(entry.id, "assistant_plan_started",
                          f"{actor} started a {len(steps)}-step assistant plan on {entry.id} (server-run)",
                          {"actor": actor, "plan_id": plan_id, "step_hash": run["step_hash"],
                           "owner": run["owner"], "claim_session": run["claim_session"],
                           "eln_project": eln_project, "origin": ORIGIN})
        live.task = asyncio.create_task(self._run(live), name=f"assistant-plan-{plan_id}")
        return _public(run, user=actor, role=headers.get("x-auth-role"))

    async def _keep_claim(self, live: _Live) -> None:
        try:
            await control._heartbeat_claim(
                self._client(), live.entry.base_url, live.entry.status_path, live.token,
                live.entry.id, live.heartbeat_s, extra_headers=live.edge_headers)
        except control._ClaimHeartbeatError as exc:
            live.claim_lost = str(exc)
            logger.warning("plan %s lost its claim: %s", live.run["plan_id"], exc)

    # -- the loop -------------------------------------------------------------

    def _halt(self, run: dict[str, Any], from_index: int, reason: str) -> None:
        for result in run["results"][from_index:]:
            if result["outcome"] == "pending":
                result["outcome"] = "skipped"
        run["halt_reason"] = reason

    async def _run(self, live: _Live) -> None:
        run, entry = live.run, live.entry
        plan_id = run["plan_id"]
        try:
            for index, step in enumerate(run["steps"]):
                while live.paused and live.abort_reason is None and live.claim_lost is None:
                    await asyncio.sleep(_PAUSE_POLL_S)
                if live.abort_reason is not None:
                    self._halt(run, index, live.abort_reason)
                    return
                if live.claim_lost is not None:
                    self._halt(run, index, f"the plan lost its device claim: {live.claim_lost}")
                    return
                result = run["results"][index]
                try:
                    status = await _read_live_status(self.app, entry.id)
                except Exception as exc:  # noqa: BLE001 - any failure halts, truthfully
                    self._halt(run, index, f"could not read {entry.id} before step {index + 1}: {exc}")
                    return
                if not _step_startable(entry, status, step["action"]):
                    allowed = sorted(getattr(status, "allowed_actions", None) or [])
                    self._halt(run, index, f"step {index + 1}: {entry.id} will not accept "
                                           f"{step['action']!r} right now (allowed: {allowed})")
                    return
                result["outcome"] = "running"
                result["started_at"] = _now()
                error = await self._save(run)
                if error:
                    result["outcome"], result["started_at"] = "pending", None
                    self._halt(run, index, f"{error}; stopped before step {index + 1} so nothing runs unrecorded")
                    return
                request = _plan_request(self.app, live.headers, plan_ref=f"{plan_id}#{index + 1}",
                                        token=live.token)
                try:
                    response = await control._proxy(
                        request, entry.id, step["passthrough_action"], "POST", step["args"])
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, default=str)
                    result.update(outcome="failed", status_code=exc.status_code,
                                  message=f"{exc.status_code}: {detail}"[:500], finished_at=_now())
                    self._halt(run, index + 1, f"step {index + 1} ({step['action']}) failed: {result['message']}")
                    return
                except Exception as exc:  # noqa: BLE001 - recorded, then halts
                    result.update(outcome="failed", message=f"{type(exc).__name__}: {exc}"[:500],
                                  finished_at=_now())
                    self._halt(run, index + 1, f"step {index + 1} ({step['action']}) failed: {result['message']}")
                    return
                result.update(outcome="ok", status_code=200, finished_at=_now())
                if keeps_response(step["action"]):
                    result["response"] = response
                error = await self._save(run)
                if error:
                    self._halt(run, index + 1, f"{error}; stopped after step {index + 1}")
                    return
                if live.claim_lost is not None:
                    self._halt(run, index + 1, f"step {index + 1} returned, but the plan then lost "
                                               f"its device claim: {live.claim_lost}")
                    return
        except asyncio.CancelledError:
            # Process shutdown. A step in flight stays "running" in the record,
            # which start-up recovery turns into "unknown".
            raise
        except Exception as exc:  # noqa: BLE001 - a bug in the loop must still end the run
            logger.exception("plan %s loop crashed", plan_id)
            for result in run["results"]:
                if result["outcome"] == "running":
                    result.update(outcome="unknown", message=f"runner error: {exc}"[:500])
            self._halt(run, 0, f"the plan runner failed: {exc}")
        finally:
            if not asyncio.current_task().cancelling():  # type: ignore[union-attr]
                await self._finish(live)

    async def _finish(self, live: _Live) -> None:
        run, entry = live.run, live.entry
        if live.heartbeat is not None:
            live.heartbeat.cancel()
            try:
                await live.heartbeat
            except (asyncio.CancelledError, control._ClaimHeartbeatError):
                pass
        await control._release_claim_best_effort(
            self._client(), entry.base_url, entry.status_path, live.token, entry.id,
            extra_headers=live.edge_headers)
        outcomes = [r["outcome"] for r in run["results"]]
        if live.abort_reason is not None and run["halt_reason"] == live.abort_reason:
            run["status"] = "aborted"
        elif all(o == "ok" for o in outcomes):
            run["status"] = "executed"
        else:
            run["status"] = "failed"
        run["paused"] = False
        run["finished_at"] = _now()
        error = await self._save(run)
        if error:
            run["halt_reason"] = f"{run.get('halt_reason') or ''} ({error})".strip()
        self.live.pop(run["plan_id"], None)
        await self.report_finished(run)

    async def report_finished(self, run: dict[str, Any]) -> None:
        """The ``assistant_plan_finished`` audit row (same shape the browser
        reported) and, with an ELN project, the notebook filing."""
        ok = sum(r["outcome"] == "ok" for r in run["results"])
        await self._audit(
            run["equipment_id"], "assistant_plan_finished",
            f"assistant plan on {run['equipment_id']} {run['status']} for {run['approved_by']} "
            f"({ok}/{len(run['results'])} steps ok, server-run)",
            {"actor": run["approved_by"], "plan_id": run["plan_id"], "step_hash": run["step_hash"],
             "status": run["status"], "executor": "server",
             "results": [{k: r.get(k) for k in ("index", "outcome", "status_code", "message")}
                         for r in run["results"]],
             "halt_reason": (run.get("halt_reason") or "")[:500] or None})
        if run.get("eln_project"):
            await self._journal_results(run)

    async def _journal_results(self, run: dict[str, Any]) -> None:
        from . import plan_artifacts, plan_results

        journal = getattr(self.app.state, "plan_results_journal", None)
        if journal is None:
            run["notebook"] = {"state": "unconfigured", "error": "plan results journal is not configured"}
            await self._save(run)
            return
        results = run["results"]
        bundle = {
            "schema": "assistant.plan_results.v1",
            "source": plan_artifacts.SOURCE_DASHBOARD,
            "plan_id": run["plan_id"],
            "device_id": f"dashboard:{run['equipment_id']}",
            "equipment_id": run["equipment_id"],
            "equipment_name": run.get("equipment_name"),
            "kind": run.get("kind"),
            "gateway_version": "",
            "simulation": False,
            "approved_by": run["approved_by"],
            "proposed_by": f"assistant (proposed to {run.get('proposed_to') or run['approved_by']})",
            "eln_project": run["eln_project"],
            "status": run["status"],
            "halt_reason": run.get("halt_reason"),
            "started_at": run["started_at"],
            "finished_at": run.get("finished_at"),
            "steps_total": len(results),
            "steps_ok": sum(r["outcome"] == "ok" for r in results),
            "steps_failed": sum(r["outcome"] in ("failed", "unknown") for r in results),
            "steps_skipped": sum(r["outcome"] == "skipped" for r in results),
            "plate_report": None,
            "plan": {
                "steps": [{"action": s["action"], "args": s["args"]} for s in run["steps"]],
                "results": [{"action": r["action"], "outcome": r["outcome"], "message": r.get("message"),
                             "reading": r.get("response"), "finished_at": r.get("finished_at")}
                            for r in results],
                "pattern": None,
            },
        }
        try:
            row = await asyncio.to_thread(journal.accept, bundle)
            run["notebook"] = {"state": row["state"], "journal": bundle["device_id"]}
            if row["state"] == plan_results.PENDING:
                asyncio.create_task(plan_results.file_pending(journal))
        except Exception as exc:  # noqa: BLE001 - the run record keeps the data either way
            logger.exception("plan %s: results not journaled", run["plan_id"])
            run["notebook"] = {"state": "error", "error": str(exc)[:300]}
        await self._save(run)

    # -- operator verbs ---------------------------------------------------------

    def _live_or_error(self, plan_id: str) -> _Live:
        live = self.live.get(plan_id)
        if live is not None:
            return live
        if self.store.get(plan_id) is not None:
            raise HTTPException(409, "this plan is no longer running")
        raise HTTPException(404, "no running plan with this id")

    async def pause(self, plan_id: str, user: str) -> dict[str, Any]:
        live = self._live_or_error(plan_id)
        async with live.lock:
            if not live.paused:
                live.paused = True
                live.run["status"] = "paused"
                live.run["paused"] = True
                live.run["paused_by"] = user
                await self._save(live.run)
                await self._audit(live.run["equipment_id"], "assistant_plan_paused",
                                  f"{user} paused assistant plan {plan_id}",
                                  {"actor": user, "plan_id": plan_id})
        return live.run

    async def resume(self, plan_id: str, user: str) -> dict[str, Any]:
        live = self._live_or_error(plan_id)
        if user != live.run["approved_by"]:
            raise HTTPException(403, "only the operator who approved this plan may resume it")
        async with live.lock:
            if live.paused:
                live.paused = False
                live.run["status"] = "executing"
                live.run["paused"] = False
                await self._save(live.run)
                await self._audit(live.run["equipment_id"], "assistant_plan_resumed",
                                  f"{user} resumed assistant plan {plan_id}",
                                  {"actor": user, "plan_id": plan_id})
        return live.run

    async def abort(self, plan_id: str, user: str, *, verb: str = "aborted") -> dict[str, Any]:
        live = self.live.get(plan_id)
        if live is None:
            # Not running: an approved plan that was never started is dropped
            # (only by whoever it was proposed to); a finished one is a no-op.
            rec = self.plans.get(plan_id)
            if rec is not None and not rec.get("starting"):
                if rec.get("actor") != user:
                    raise HTTPException(403, "this plan was proposed to a different operator")
                self.plans.pop(plan_id, None)
                return {"plan_id": plan_id, "status": "aborted", "started": False}
            run = self.store.get(plan_id)
            if run is not None:
                return run
            raise HTTPException(404, "no plan with this id")
        if live.abort_reason is None:
            live.abort_reason = f"{verb} by {user}"
            await self._audit(live.run["equipment_id"], f"assistant_plan_{verb}",
                              f"{user} {verb} assistant plan {plan_id}",
                              {"actor": user, "plan_id": plan_id})
        return live.run

    async def shutdown(self) -> None:
        """Process exit: stop the loops without finishing the runs, so their
        records keep the truth (a step in flight stays ``running`` and becomes
        ``unknown`` on the next start). Between steps the claim is released;
        with a step in flight it is left to lapse at its TTL instead, so the
        device is not offered to anyone while it may still be moving."""
        for live in list(self.live.values()):
            in_flight = any(r["outcome"] == "running" for r in live.run["results"])
            for task in (live.task, live.heartbeat):
                if task is not None:
                    task.cancel()
            if not in_flight:
                await control._release_claim_best_effort(
                    self._client(), live.entry.base_url, live.entry.status_path, live.token,
                    live.entry.id, extra_headers=live.edge_headers)
        self.live.clear()

    async def recover(self) -> None:
        for run in await asyncio.to_thread(self.store.recover_interrupted):
            logger.warning("assistant plan %s was interrupted by a restart", run["plan_id"])
            await self.report_finished(run)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _runner(request: Request) -> PlanRunner:
    runner = getattr(request.app.state, "assistant_plan_runner", None)
    if runner is None:
        raise HTTPException(503, "server-side plan execution is not configured")
    return runner


def _signed_in(request: Request) -> str:
    user = request.headers.get("x-auth-user")
    if not user:
        raise HTTPException(401, "Sign in to see or stop a plan.")
    return user


def build_plan_runs_router() -> APIRouter:
    # Under /api/assistant/: the dashboard middleware requires a signed-in
    # session there and stamps the verified X-Auth-User / X-Auth-Role.
    router = APIRouter(prefix="/api/assistant", tags=["assistant"])

    @router.post("/plans/{plan_id}/execute", status_code=202)
    async def execute_plan(plan_id: str, request: Request, body: PlanExecuteRequest) -> dict[str, Any]:
        """Start an approved plan on the server. The approver only, from a
        signed-in browser session, holding authorization on the device; the
        plan then holds the device claim until it ends, whether or not the
        page stays open. See the module docstring for every rule."""
        return await _runner(request).execute(plan_id, request, body)

    @router.get("/plans/{plan_id}/run")
    async def get_run(plan_id: str, request: Request) -> dict[str, Any]:
        """A run's live state and per-step results (poll this to follow it)."""
        user = _signed_in(request)
        runner = _runner(request)
        live = runner.live.get(plan_id)
        run = live.run if live is not None else await asyncio.to_thread(runner.store.get, plan_id)
        if run is None:
            raise HTTPException(404, "no run with this id")
        return _public(run, user=user, role=request.headers.get("x-auth-role"))

    @router.get("/plan-runs")
    async def list_runs(request: Request, equipment_id: str | None = None,
                        active: bool = False, limit: int = 20) -> list[dict[str, Any]]:
        """Recent server-run plans, newest first, as summaries (no results).
        Reconnecting tabs use ``?active=true`` to find a plan still running."""
        _signed_in(request)
        runner = _runner(request)
        rows = await asyncio.to_thread(runner.store.list, limit=max(1, min(limit, 200)),
                                       equipment_id=equipment_id, active_only=active)
        out = []
        for row in rows:
            live = runner.live.get(row["plan_id"])
            out.append(_summary(live.run if live is not None else row))
        return out

    @router.post("/plans/{plan_id}/pause")
    async def pause_run(plan_id: str, request: Request) -> dict[str, Any]:
        """Hold at the next step boundary (the step in flight completes). The
        plan keeps the device claim while paused. Anyone signed in."""
        user = _signed_in(request)
        return _summary(await _runner(request).pause(plan_id, user))

    @router.post("/plans/{plan_id}/resume")
    async def resume_run(plan_id: str, request: Request) -> dict[str, Any]:
        """Continue a paused plan. Only its approver: resuming moves hardware."""
        user = _signed_in(request)
        return _summary(await _runner(request).resume(plan_id, user))

    @router.post("/plans/{plan_id}/stop")
    async def stop_run(plan_id: str, request: Request) -> dict[str, Any]:
        """End the plan after the step in flight; the rest are skipped and
        the claim is released. Anyone signed in. Not a hardware stop."""
        user = _signed_in(request)
        return _summary_or(await _runner(request).abort(plan_id, user, verb="stopped"))

    @router.post("/plans/{plan_id}/abort")
    async def abort_run(plan_id: str, request: Request) -> dict[str, Any]:
        """Like stop; also discards an approved plan that has not started."""
        user = _signed_in(request)
        return _summary_or(await _runner(request).abort(plan_id, user))

    return router


def _summary_or(run: dict[str, Any]) -> dict[str, Any]:
    return _summary(run) if "results" in run else run


def plan_runs_path(db_path: Path) -> Path:
    return Path(os.environ.get("ASSISTANT_PLAN_RUNS_PATH") or db_path.with_name("assistant_plan_runs.sqlite3"))
