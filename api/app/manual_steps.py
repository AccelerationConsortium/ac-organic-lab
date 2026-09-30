"""Run-scoped human acknowledgments. No hardware commands; no automatic resume.

The SQLite journal preserves requests/decisions across process loss. BitacoraDB
remains the scientific record and custody authority. Only a confirmed ledger
write releases the executor; an ambiguous write keeps the same request/key.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .custody import observe, reconcile


class ManualDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=80)
    outcome: Literal["done", "failed"]
    note: str = Field(default="", max_length=4000)


class ManualJournal:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS manual_requests (request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_id TEXT NOT NULL, project_id TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL, decision TEXT, result TEXT, UNIQUE(run_id, step_id))")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        # A saved acknowledgment is evidence, never a resumable hardware job.
        with self.connect() as db:
            db.execute("UPDATE manual_requests SET state='interrupted' WHERE state IN ('waiting','acknowledged','uncertain')")

    def create(self, run_id, step_id, project_id, payload):
        rid = uuid4().hex
        payload = {**payload, "request_id": rid, "since": datetime.now(timezone.utc).isoformat()}
        with self.connect() as db:
            db.execute("INSERT INTO manual_requests VALUES (?,?,?,?,?,?,NULL,NULL)",
                       (rid, run_id, step_id, project_id, "waiting", json.dumps(payload)))
        return self.get(rid)

    def get(self, rid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM manual_requests WHERE request_id=?", (rid,)).fetchone()
        if row is None:
            return None
        return {**dict(row), **json.loads(row["payload"]),
                "decision": json.loads(row["decision"]) if row["decision"] else None,
                "result": json.loads(row["result"]) if row["result"] else None}

    def for_run(self, run_id):
        with self.connect() as db:
            ids = db.execute("SELECT request_id FROM manual_requests WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
        return [self.get(row[0]) for row in ids]

    def decide(self, rid, who, body):
        value = {"by": who, "outcome": body.outcome, "note": body.note}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT decision,state FROM manual_requests WHERE request_id=?", (rid,)).fetchone()
            if row is None:
                raise HTTPException(404, "No manual request")
            if row["decision"]:
                previous = json.loads(row["decision"])
                if any(previous[k] != v for k, v in value.items()):
                    raise HTTPException(409, "This manual request already has a different decision")
            else:
                if row["state"] != "waiting":
                    raise HTTPException(409, "This request is no longer waiting; reconcile before a new run")
                value["at"] = datetime.now(timezone.utc).isoformat()
                db.execute("UPDATE manual_requests SET decision=?,state='acknowledged' WHERE request_id=?",
                           (json.dumps(value), rid))
        return self.get(rid)

    def finish(self, rid, state, result=None):
        with self.connect() as db:
            db.execute("UPDATE manual_requests SET state=?,result=? WHERE request_id=?",
                       (state, json.dumps(result), rid))
        return self.get(rid)


def journal(request: Request) -> ManualJournal:
    store = getattr(request.app.state, "manual_journal", None)
    if store is None:
        raise HTTPException(503, "Manual acknowledgment journal is not configured")
    return store


async def member_scope(request: Request, who: str) -> dict:
    """Resolve standing from the roster, never caller-supplied project headers."""
    from .control import _authz_base
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{_authz_base()}/authz/scope", params={"user": who})
            response.raise_for_status()
            scope = response.json()
            if not isinstance(scope, dict) or scope.get("user") != who:
                raise ValueError("invalid project scope")
            return scope
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "Project membership could not be verified") from exc


def may_confirm(scope, project):
    return scope.get("is_admin") is True or any(
        isinstance(scope.get(key), list) and project in scope[key]
        for key in ("member_projects", "pi_projects"))


def check_manual_package(auth, locations):
    """Every instrument touched by a transfer must be in its access claims."""
    for step in auth.steps:
        if step.get("kind") != "manual":
            continue
        spec = step.get("custody")
        if not spec:
            continue
        if locations is None or not spec.get("from") or not spec.get("hid"):
            raise ValueError("Manual transfers require registered source/destination and a physical plate")
        covered = {auth.binding.get(role) for role in step["manual"].get("access_roles", [])}
        for name in (spec["from"], spec["to"]):
            entry = locations.by_name(name)
            if entry is None or not entry.active:
                raise ValueError(f"Manual transfer location {name!r} is not active")
            if entry.equipment and entry.equipment not in covered:
                raise ValueError(f"Manual access_roles must claim equipment {entry.equipment!r}")


async def perform_manual(step, *, state, request, auth, recorder, locations, gate):
    from lab_skills.plan import ManualOutcome
    store = journal(request)
    spec = auth.custody_by_step.get(step.id)
    if spec:
        if recorder is None:
            raise ValueError("Manual transfer requires the custody ledger")
        current = await recorder.current_location(spec["hid"], user=state.launched_by,
                                                  project=auth.project_id, refresh=True)
        if current.get("found") is not True or current.get("location_name") != spec["from"]:
            raise ValueError("Manual transfer source does not match the current custody ledger")
    row = store.create(state.run_id, step.id, auth.project_id,
        {"step_id": step.id, "manual": step.manual.model_dump(), "custody": spec,
         "plan_id": state.record["plan_id"], "authorization_id": auth.authorization_id})
    rid = row["request_id"]
    state.waiting_on = row
    state.manual_changed.clear()
    state.emit("manual", row)
    try:
        while True:
            if state.abort_requested:
                raise ValueError(f"aborted by {state.abort_requested}; reconcile any physical work")
            row = store.get(rid)
            decision = row["decision"]
            if decision:
                if decision["outcome"] == "failed":
                    store.finish(rid, "failed", {"reason": decision["note"]})
                    return ManualOutcome(outcome="failed", confirmed_by=decision["by"],
                                         note=decision["note"], request_id=rid)
                if spec:
                    result = await recorder.record_move(hid=spec["hid"], to=spec["to"],
                        expected_from=spec["from"], client_action_id=f"manual:{rid}",
                        performed_by=decision["by"], recorder=decision["by"], project=auth.project_id,
                        plan_id=state.record["plan_id"], step_id=step.id,
                        params={"run_id": state.run_id, "authorization_id": auth.authorization_id,
                                "via": "manual", "note": decision["note"]})
                    if result.get("recorded") is not True:
                        row = store.finish(rid, "uncertain", result)
                        state.waiting_on = row
                        state.emit("manual", row)
                        # Retry requires the SAME acknowledgment from the UI.
                        # No timer repeats a write or asks for another move.
                        state.manual_changed.clear()
                        while not state.manual_changed.is_set():
                            if state.abort_requested:
                                raise ValueError("Aborted with unresolved custody; reconcile before another run")
                            reason = await gate(None)
                            if reason:
                                raise ValueError(reason)
                            try:
                                await asyncio.wait_for(state.manual_changed.wait(), 2)
                            except asyncio.TimeoutError:
                                pass
                        continue
                    state.custody_expected[spec["hid"]] = spec["to"]
                    state.manual_plates.add(spec["hid"])
                    entry = locations.by_name(spec["to"])
                    if entry.equipment:
                        aggregator = getattr(request.app.state, "aggregator", None)
                        if aggregator is None:
                            raise ValueError("Destination device observation is unavailable")
                        snapshot = await aggregator.fetch_one(entry.equipment)
                        if snapshot is None or getattr(snapshot, "fetch_error", None) or getattr(snapshot, "status", None) is None:
                            raise ValueError("Destination device could not be read")
                        observed = observe(snapshot, entry, locations)
                        verdict = reconcile(spec["hid"], observed)
                        state.emit("custody", {"step_id": step.id, "recorded": True,
                            "verdict": verdict, "observed": observed.as_dict(), "result": result})
                        if verdict == "mismatch":
                            raise ValueError("Human reported completion, but destination device contradicts it")
                reason = await gate(None)
                if reason:
                    raise ValueError(reason)
                store.finish(rid, "completed", {"custody_recorded": bool(spec)})
                return ManualOutcome(outcome="completed", confirmed_by=decision["by"],
                                     note=decision["note"], request_id=rid)
            reason = await gate(None)
            if reason:
                raise ValueError(reason)
            try:
                await asyncio.wait_for(state.manual_changed.wait(), 2)
            except asyncio.TimeoutError:
                pass
            state.manual_changed.clear()
    finally:
        row = store.get(rid)
        if row["state"] in {"waiting", "acknowledged", "uncertain"}:
            row = store.finish(rid, "interrupted", row.get("result"))
        state.custody_notes.append({"kind": "manual_action", "step_id": step.id,
            "body": f"Manual action {row['state']}; physical work is reported by the acknowledging person.",
            "data": {"request_id": rid, "decision": row["decision"], "result": row["result"]}})
        state.waiting_on = None
        state.emit("manual_resolved", row)
