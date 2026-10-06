"""OT-2 plan results -> BitacoraDB, filed as *unformatted* Experiments.

An operator approves an assistant-proposed step list in an OT-2 gateway panel
and the plan runs on the device (``opentrons-server`` ``gateway/plans.py``).
That is a device-local step approval, not a Run Authorization, and until this
module its measurements lived only in the gateway's memory. Now the gateway
saves each finished plan and pushes it here; this files it in the ELN for the
approver to curate later.

Three properties this file holds:

1. **Accepted means durable.** A bundle is written to the SQLite journal
   before the gateway is told ``accepted``; the gateway then stops retrying.
   Filing into BitacoraDB is retried from here, across restarts.

2. **The record is truthful about what it is.** The Experiment says
   ``UNFORMATTED`` and its ``meta`` records an assistant proposal approved in a
   device panel — never a Run Authorization, never a completed protocol Plan.
   No BitacoraDB Plan row is written: a plan that already ran cannot honestly
   pass through ``draft -> approved`` there. Simulations are refused outright.

3. **Identity is checked here, not trusted from the device.** A gateway proves
   *which device* it is with its token (``PLAN_RESULTS_DEVICE_TOKENS``); the
   approver's membership of the chosen project is then checked against the
   roster (``/authz/scope``), as ``manual_steps.member_scope`` does. A bundle
   whose approver is not a member is held with that reason, never filed.

Configuration: ``PLAN_RESULTS_DEVICE_TOKENS`` (JSON ``{device_id: token}``;
unset refuses every push), plus the record layer's own ``BITACORADB_URL`` /
``BITACORADB_EDGE_SECRET_PATH`` (``record.py``) and ``AUTH_SERVICE_BASE``.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import record

logger = logging.getLogger(__name__)

RETRY_INTERVAL_S = 60.0

PENDING = "pending"  # accepted, not yet filed (or a transient failure)
FILED = "filed"
HELD = "held"  # needs a human: not a member, unknown project, rejected row


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlanResultsBundle(BaseModel):
    """What ``opentrons-server`` ``gateway/plan_results.py`` sends."""

    model_config = ConfigDict(extra="allow")

    schema_: Literal["ot2.plan_results.v1"] = Field(alias="schema")
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    device_id: str = Field(min_length=1, max_length=80)
    equipment_id: str = Field(min_length=1, max_length=80)
    gateway_version: str = ""
    simulation: bool
    approved_by: str = Field(min_length=1, max_length=200)
    proposed_by: Optional[str] = None
    eln_project: str = Field(min_length=1, max_length=200)
    status: str
    halt_reason: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    steps_total: int
    steps_ok: int
    steps_failed: int
    steps_skipped: int
    plate_report: Optional[dict[str, Any]] = None
    plan: dict[str, Any]


def device_tokens() -> dict[str, str]:
    raw = os.environ.get("PLAN_RESULTS_DEVICE_TOKENS", "").strip()
    if not raw:
        return {}
    tokens = json.loads(raw)
    if not isinstance(tokens, dict):
        raise ValueError("PLAN_RESULTS_DEVICE_TOKENS must be a JSON object")
    return {str(k): str(v) for k, v in tokens.items()}


class PlanResultsJournal:
    """One row per (device, plan): the frozen bundle and its filing state."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS plan_results ("
                " device_id TEXT NOT NULL, plan_id TEXT NOT NULL, state TEXT NOT NULL,"
                " payload TEXT NOT NULL, received_at TEXT NOT NULL, attempts INTEGER NOT NULL,"
                " last_error TEXT, experiment_id TEXT, note_id TEXT, filed_at TEXT,"
                " PRIMARY KEY (device_id, plan_id))")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def accept(self, bundle: dict[str, Any]) -> dict[str, Any]:
        """Store a bundle once. A retry of the same bundle is a no-op; a
        different bundle under the same plan id is refused (409)."""
        payload = json.dumps(bundle, sort_keys=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM plan_results WHERE device_id=? AND plan_id=?",
                             (bundle["device_id"], bundle["plan_id"])).fetchone()
            if row is None:
                db.execute("INSERT INTO plan_results VALUES (?,?,?,?,?,0,NULL,NULL,NULL,NULL)",
                           (bundle["device_id"], bundle["plan_id"], PENDING, payload, _now()))
            elif row["payload"] != payload:
                raise HTTPException(409, "a different result bundle was already accepted for this plan")
        return self.get(bundle["device_id"], bundle["plan_id"])

    def get(self, device_id: str, plan_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM plan_results WHERE device_id=? AND plan_id=?",
                             (device_id, plan_id)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["payload"] = json.loads(out["payload"])
        return out

    def pending(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            keys = db.execute("SELECT device_id, plan_id FROM plan_results WHERE state=?"
                              " ORDER BY received_at", (PENDING,)).fetchall()
        return [self.get(k["device_id"], k["plan_id"]) for k in keys]

    def summaries(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT device_id, plan_id, state, received_at, attempts, last_error,"
                " experiment_id, note_id, filed_at FROM plan_results"
                " ORDER BY received_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def update(self, device_id: str, plan_id: str, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.connect() as db:
            db.execute(f"UPDATE plan_results SET {cols} WHERE device_id=? AND plan_id=?",
                       (*fields.values(), device_id, plan_id))


# ---------------------------------------------------------------------------
# Filing
# ---------------------------------------------------------------------------


def experiment_hid(bundle: dict[str, Any]) -> str:
    """Stable per plan, so a retried filing finds its own Experiment."""
    return f"{bundle['equipment_id']}-plan-{bundle['plan_id']}"


#: Bumped when the generated artefacts change shape; the back-fill re-files
#: experiments whose meta carries an older (or no) version.
ARTIFACTS_VERSION = 3


def experiment_artifacts(bundle: dict[str, Any]) -> dict[str, Any]:
    """What the run can truthfully tell the notebook, in Bitácora's formats
    (plan_artifacts.py): the as-run protocol + action map, the design
    skeleton with its TODOs, and the artefact version."""
    from . import plan_artifacts

    files = plan_artifacts.protocol_yaml(bundle)
    return {
        "artifacts_version": ARTIFACTS_VERSION,
        "as_run_protocol": {"name": plan_artifacts.artifact_name(bundle),
                            "protocol_yaml": files["protocol"], "actions_yaml": files["actions"],
                            "warnings": files["warnings"]},
        "design_skeleton": plan_artifacts.design_skeleton(bundle),
    }


def experiment_meta(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        **experiment_artifacts(bundle),
        "unformatted": True,
        "created_by": "ac-organic-lab/plan_results",
        "source": "ot2-gateway plan",
        "approval": "OT-2 gateway step approval (not a Run Authorization)",
        "plan_id": bundle["plan_id"],
        "device_id": bundle["device_id"],
        "equipment_id": bundle["equipment_id"],
        "gateway_version": bundle.get("gateway_version"),
        "approved_by": bundle["approved_by"],
        "proposed_by": bundle.get("proposed_by"),
        "status": bundle["status"],
    }


def note_body(bundle: dict[str, Any]) -> str:
    stats = ((bundle.get("plate_report") or {}).get("stats") or {})
    weighed = ""
    if stats.get("n"):
        weighed = (f" {stats['n']} wells weighed: mean {stats.get('mean_g')} g, "
                   f"range {stats.get('min_g')}-{stats.get('max_g')} g, CV {stats.get('cv_pct')}%.")
    halt = f" Halted: {bundle['halt_reason']}." if bundle.get("halt_reason") else ""
    return (
        f"UNFORMATTED results of OT-2 plan {bundle['plan_id']} on {bundle['equipment_id']}: "
        f"{bundle['status']}, {bundle['steps_ok']}/{bundle['steps_total']} steps ok "
        f"({bundle['steps_failed']} failed, {bundle['steps_skipped']} skipped).{halt}{weighed} "
        f"Proposed by {bundle.get('proposed_by') or 'unknown'}; approved and run from the OT-2 "
        f"panel by {bundle['approved_by']} (a device-local step approval, not a Run "
        f"Authorization). Curate this Experiment: retitle it and add notes as needed."
    )


def note_data(bundle: dict[str, Any]) -> dict[str, Any]:
    plan = bundle.get("plan") or {}
    steps = plan.get("steps") or []
    results = plan.get("results") or []
    step_results = [
        {"index": i + 1, "action": r.get("action"),
         "args": (steps[i] or {}).get("args") if i < len(steps) else None,
         "outcome": r.get("outcome"), "message": r.get("message"),
         "reading": r.get("reading"), "finished_at": r.get("finished_at")}
        for i, r in enumerate(results)
    ]
    return {
        "source": "ot2-gateway plan",
        **{k: bundle.get(k) for k in (
            "plan_id", "device_id", "equipment_id", "gateway_version", "status", "halt_reason",
            "started_at", "finished_at", "steps_total", "steps_ok", "steps_failed",
            "steps_skipped", "approved_by", "proposed_by")},
        "plate_report": bundle.get("plate_report"),
        "step_results": step_results,
    }


class Held(Exception):
    """Not retryable without a human: recorded as the bundle's reason."""


async def user_scope(client: httpx.AsyncClient, user: str) -> dict[str, Any]:
    """The user's standing from the roster (never from caller headers)."""
    from .control import _authz_base

    r = await client.get(f"{_authz_base()}/authz/scope", params={"user": user})
    r.raise_for_status()
    scope = r.json()
    if not isinstance(scope, dict) or scope.get("user") != user:
        raise ValueError("invalid project scope from the auth service")
    return scope


async def ensure_artifacts(client: httpx.AsyncClient, base: str, headers: dict[str, str],
                           experiment_id: str, bundle: dict[str, Any],
                           meta: Optional[dict[str, Any]] = None) -> dict[str, int]:
    """Bring one filed Experiment up to the current artefacts: its meta carries
    the as-run protocol and design skeleton, and every weighed well has a
    Sample. Idempotent — meta is PATCHed only when its version is behind, and
    only wells whose ``hid`` is not there yet are posted — so it serves both
    the filing path and the back-fill. Returns what it did."""
    from . import plan_artifacts

    done = {"meta_patched": 0, "samples_posted": 0}
    if meta is None:
        r = await client.get(f"{base}/experiments/{experiment_id}", headers=headers)
        r.raise_for_status()
        meta = (r.json().get("meta") or {})
    if int(meta.get("artifacts_version") or 0) < ARTIFACTS_VERSION:
        r = await client.patch(f"{base}/experiments/{experiment_id}", headers=headers,
                               json={"meta": {**meta, **experiment_artifacts(bundle)}})
        if r.status_code == 422:
            raise Held(f"BitacoraDB refused the artefacts: {r.text[:300]}")
        r.raise_for_status()
        done["meta_patched"] = 1
    wanted = plan_artifacts.well_samples(bundle)
    if wanted:
        r = await client.get(f"{base}/samples", headers=headers, params={"experiment_id": experiment_id})
        r.raise_for_status()
        present = {str(s.get("hid")) for s in r.json()}
        for sample in wanted:
            if sample["hid"] in present:
                continue
            r = await client.post(f"{base}/samples", headers=headers,
                                  json={**sample, "experiment_id": experiment_id})
            if r.status_code == 422:
                raise Held(f"BitacoraDB refused a well sample: {r.text[:300]}")
            if r.status_code != 409:
                r.raise_for_status()
                done["samples_posted"] += 1
    return done


async def approver_is_member(client: httpx.AsyncClient, user: str, project: str) -> bool:
    from .manual_steps import may_confirm

    return may_confirm(await user_scope(client, user), project)


async def file_one(journal: PlanResultsJournal, row: dict[str, Any],
                   client: httpx.AsyncClient) -> str:
    """File one accepted bundle; returns its new state. Never raises."""
    bundle = row["payload"]
    device_id, plan_id = row["device_id"], row["plan_id"]
    attempts = row["attempts"] + 1
    base, secret = record.BITACORADB_URL.rstrip("/"), record.edge_secret()
    if not base or not secret:
        journal.update(device_id, plan_id, attempts=attempts,
                       last_error="record layer not configured (BITACORADB_URL / edge secret)")
        return PENDING
    user, project = bundle["approved_by"], bundle["eln_project"]
    headers = {"X-Edge-Secret": secret, "X-Auth-User": user, "X-Auth-Projects": project}
    try:
        if not await approver_is_member(client, user, project):
            raise Held(f"{user} is not a member of ELN project {project!r}")

        experiment_id = row["experiment_id"]
        if experiment_id is None:
            hid = experiment_hid(bundle)
            started = bundle.get("started_at") or row["received_at"]
            title = f"UNFORMATTED — OT-2 plan {plan_id} ({started[:10]})"

            found_meta: Optional[dict[str, Any]] = None

            async def _find() -> Optional[str]:
                nonlocal found_meta
                r = await client.get(f"{base}/experiments", headers=headers,
                                     params={"project": project, "hid": hid})
                r.raise_for_status()
                rows = r.json()
                if rows:
                    found_meta = rows[0].get("meta") or {}
                    return str(rows[0]["experiment_id"])
                return None

            experiment_id = await _find()
            if experiment_id is None:
                r = await client.post(f"{base}/experiments", headers=headers, json={
                    "hid": hid, "title": title, "project": project, "operator": user,
                    "creator": user, "started_at": started, "meta": experiment_meta(bundle)})
                if r.status_code == 422:
                    raise Held(f"BitacoraDB refused the Experiment: {r.text[:300]}")
                if r.status_code == 409:
                    experiment_id = await _find()
                    if experiment_id is None:
                        raise RuntimeError(f"experiment {hid!r} exists (409) but is not readable")
                else:
                    r.raise_for_status()
                    experiment_id = str(r.json()["experiment_id"])
                    found_meta = experiment_meta(bundle)  # just written: nothing to patch
            journal.update(device_id, plan_id, experiment_id=experiment_id)
            known_meta = found_meta
        else:
            known_meta = None  # a retry: read the live meta before deciding

        # Notes are append-only and carry no idempotency key, so a crash after
        # a successful POST but before the journal update would file the note
        # twice. The Experiment is per plan, so this plan's note is found by
        # its data before posting another.
        r = await client.get(f"{base}/notes", headers=headers,
                             params={"experiment_id": experiment_id, "kind": "observation"})
        r.raise_for_status()
        existing = next(
            (n for n in r.json()
             if (n.get("data") or {}).get("source") == "ot2-gateway plan"
             and (n.get("data") or {}).get("plan_id") == plan_id
             and (n.get("data") or {}).get("device_id") == device_id),
            None,
        )
        if existing is not None:
            note_id = str(existing["note_id"])
        else:
            r = await client.post(f"{base}/notes", headers=headers, json={
                "experiment_id": experiment_id, "kind": "observation", "creator": user,
                "body": note_body(bundle), "data": note_data(bundle)})
            if r.status_code == 422:
                raise Held(f"BitacoraDB refused the note: {r.text[:300]}")
            r.raise_for_status()
            note_id = str(r.json()["note_id"])
        # The artefacts the notebook can import from (UNFORMATTED_RUNS_PLAN.md
        # step 1): as-run protocol + design skeleton on the Experiment, one
        # Sample per weighed well. Idempotent, so a retry completes them.
        await ensure_artifacts(client, base, headers, experiment_id, bundle, meta=known_meta)
        journal.update(device_id, plan_id, state=FILED, attempts=attempts, last_error=None,
                       note_id=note_id, filed_at=_now())
        return FILED
    except Held as exc:
        journal.update(device_id, plan_id, state=HELD, attempts=attempts, last_error=str(exc))
        logger.warning("plan results %s/%s held: %s", device_id, plan_id, exc)
        return HELD
    except Exception as exc:  # noqa: BLE001 — transient; retried by the loop
        journal.update(device_id, plan_id, attempts=attempts, last_error=str(exc)[:500])
        logger.warning("plan results %s/%s not filed (attempt %d): %s",
                       device_id, plan_id, attempts, exc)
        return PENDING


async def file_pending(journal: PlanResultsJournal) -> int:
    filed = 0
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        for row in journal.pending():
            if await file_one(journal, row, client) == FILED:
                filed += 1
    return filed


async def retry_loop(journal: PlanResultsJournal, interval_s: float = RETRY_INTERVAL_S) -> None:
    while True:
        try:
            await file_pending(journal)
        except Exception:  # noqa: BLE001 — keep the loop alive; the rows carry the error
            logger.exception("plan results filing pass failed")
        await asyncio.sleep(interval_s)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _journal(request: Request) -> PlanResultsJournal:
    journal = getattr(request.app.state, "plan_results_journal", None)
    if journal is None:
        raise HTTPException(503, "plan results journal is not configured")
    return journal


def build_plan_results_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["plan-results"])

    @router.post("/ingest/plan-results", status_code=202)
    async def ingest_plan_results(body: PlanResultsBundle, request: Request,
                                  authorization: str = Header(default="")) -> dict[str, Any]:
        """Accept one finished OT-2 plan's results for filing in the ELN.

        The device proves its identity with its bearer token; unconfigured
        tokens refuse every push. ``accepted`` means stored durably here.
        """
        expected = device_tokens().get(body.device_id)
        presented = authorization.removeprefix("Bearer ").strip()
        if not expected or not hmac.compare_digest(presented, expected):
            raise HTTPException(401, "unknown device or bad token")
        if body.simulation:
            raise HTTPException(422, "simulated plans never enter the record")
        journal = _journal(request)
        row = journal.accept(body.model_dump(mode="json", by_alias=True))
        if row["state"] == PENDING:
            # File now rather than at the next loop tick; the loop is the backstop.
            asyncio.create_task(file_pending(journal))
        return {"status": "accepted", "plan_id": body.plan_id, "state": row["state"]}

    @router.post("/plan-results/backfill")
    async def backfill_artifacts(request: Request) -> dict[str, Any]:
        """Bring every already-filed run up to the current artefacts (as-run
        protocol, design skeleton, well samples). Admin only — it writes to
        the ELN on the approvers' behalf, with each approver's own standing,
        exactly as the original filing did. Idempotent; safe to re-run."""
        if request.headers.get("x-auth-role") != "admin" or not request.headers.get("x-auth-user"):
            raise HTTPException(403, "admin only")
        journal = _journal(request)
        base, secret = record.BITACORADB_URL.rstrip("/"), record.edge_secret()
        if not base or not secret:
            raise HTTPException(503, "record layer not configured")
        out: dict[str, Any] = {"examined": 0, "meta_patched": 0, "samples_posted": 0, "errors": []}
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            for row in journal.summaries(limit=1000):
                if row.get("state") != FILED:
                    continue
                full = journal.get(row["device_id"], row["plan_id"]) or {}
                bundle, experiment_id = full.get("payload") or {}, full.get("experiment_id")
                if not bundle or not experiment_id:
                    continue
                out["examined"] += 1
                headers = {"X-Edge-Secret": secret, "X-Auth-User": bundle["approved_by"],
                           "X-Auth-Projects": bundle["eln_project"]}
                try:
                    done = await ensure_artifacts(client, base, headers, str(experiment_id), bundle)
                    out["meta_patched"] += done["meta_patched"]
                    out["samples_posted"] += done["samples_posted"]
                except Exception as exc:  # noqa: BLE001 - reported per run, never hidden
                    out["errors"].append({"plan_id": row["plan_id"], "error": str(exc)[:300]})
        return out

    # Under /api/assistant/ so the dashboard middleware requires a signed-in
    # session and stamps the verified X-Auth-User (the old /api/plan-results
    # was outside it and answered anyone). Rows are filtered like the device
    # filters its run records: the approver, members/PIs of the run's ELN
    # project, and admins.
    @router.get("/assistant/plan-results")
    async def list_plan_results(request: Request) -> list[dict[str, Any]]:
        """Recent OT-2 plan results you may see, and whether each reached the ELN."""
        user = request.headers.get("x-auth-user")
        if not user:
            raise HTTPException(401, "Sign in to list plan results.")
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                scope = await user_scope(client, user)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, f"Could not verify your projects: {exc}") from exc
        standing = set(scope.get("member_projects") or []) | set(scope.get("pi_projects") or [])
        admin = scope.get("is_admin") is True
        journal = _journal(request)
        out = []
        for row in journal.summaries():
            full = journal.get(row["device_id"], row["plan_id"]) or {}
            bundle = full.get("payload") or {}
            if admin or bundle.get("approved_by") == user or bundle.get("eln_project") in standing:
                out.append({**row, "approved_by": bundle.get("approved_by"),
                            "eln_project": bundle.get("eln_project")})
        return out

    @router.get("/ingest/plan-results/{device_id}/{plan_id}")
    async def plan_result_status(device_id: str, plan_id: str, request: Request,
                                 authorization: str = Header(default="")) -> dict[str, Any]:
        """What became of one accepted bundle — for the device that sent it.

        ``accepted`` only ever meant "journaled here"; this is how the device
        learns whether it was *filed* or *held* (and why), so it never shows
        "filed" for a run the ELN refused. Same per-device token as the push.
        """
        expected = device_tokens().get(device_id)
        presented = authorization.removeprefix("Bearer ").strip()
        if not expected or not hmac.compare_digest(presented, expected):
            raise HTTPException(401, "unknown device or bad token")
        row = _journal(request).get(device_id, plan_id)
        if row is None:
            raise HTTPException(404, "no such bundle from this device")
        return {key: row[key] for key in (
            "plan_id", "state", "attempts", "last_error", "experiment_id", "note_id", "filed_at")}

    # Under /api/assistant/ on purpose: the dashboard's middleware gates that
    # prefix — a signed-in session is required and X-Auth-User is replaced by
    # the verified identity — so this answers for the real user, never for a
    # header a client typed.
    @router.get("/assistant/eln-projects")
    async def eln_projects(request: Request) -> dict[str, Any]:
        """ELN projects the signed-in user can file instrument results into:
        projects that exist in BitacoraDB *and* where the roster gives the
        user standing (member, PI, or admin). The approval card's picker."""
        user = request.headers.get("x-auth-user")
        if not user:
            raise HTTPException(401, "Sign in to list your ELN projects.")
        base, secret = record.BITACORADB_URL.rstrip("/"), record.edge_secret()
        if not base or not secret:
            return {"configured": False, "projects": []}
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                scope = await user_scope(client, user)
                standing = set(scope.get("member_projects") or []) | set(scope.get("pi_projects") or [])
                admin = scope.get("is_admin") is True
                headers = {"X-Edge-Secret": secret, "X-Auth-User": user,
                           "X-Auth-Projects": ",".join(sorted(standing))}
                if admin:
                    headers["X-Auth-Role"] = "admin"
                r = await client.get(f"{base}/projects", headers=headers)
                r.raise_for_status()
                titles = {row["title"] for row in r.json() if isinstance(row, dict) and row.get("title")}
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise HTTPException(503, f"Could not list ELN projects: {exc}") from exc
        allowed = titles if admin else titles & standing
        return {"configured": True, "projects": sorted(allowed)}

    return router
