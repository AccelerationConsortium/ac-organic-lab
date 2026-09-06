"""BitacoraDB record-layer writes for authorized runs — design decision D-23.

The executor already *produces* the record layer's shapes on every run
(`plan_row_from` / `notes_from` in `workflow.py`); this module is what finally
writes them. Until it existed, a run was durable only as a coarse `plan_run`
audit row — "14 steps, ok, 141 s" — while the per-step trail lived in the SSE
stream and an in-memory registry, both of which die with the process. You could
prove *what was authorized* and *that it ran*, but not reconstruct which step
failed and why.

Three properties this file exists to hold:

1. **A record-write failure never fails the run.** The run is physical and has
   already happened. Raising here would turn "the plate was sealed but we could
   not file the paperwork" into a crashed run, which is a worse lie than a
   missing row. Every path returns a status dict; nothing propagates.

2. **An Experiment is always ensured, so failure notes are never dropped.**
   `NoteCreate` requires an `experiment_id` (`PlanCreate` does not), so a run
   with no resolvable Experiment could file its Plan and silently lose exactly
   the notes that matter — the deviations. A design *is* an Experiment, so its
   slug is the identity when the protocol carries `design_ref`; a protocol
   without a shared design is still an experiment, identified by its own path.

3. **Disabled unless configured.** No `BITACORADB_URL` means every call is a
   no-op reporting `not_configured`, so deploying this changes nothing until
   the deployment opts in.

Auth is BitacoraDB's model, matching bitácora's `RecordLayer`: a shared
`X-Edge-Secret` proves the request came through a trusted front, and
`X-Auth-User` carries the human it is for.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Empty disables the whole feature (property 3 above).
BITACORADB_URL = os.environ.get("BITACORADB_URL", "")
#: File-based like bitácora's, so the secret stays out of the systemd unit.
BITACORADB_EDGE_SECRET_PATH = os.environ.get("BITACORADB_EDGE_SECRET_PATH", "")


def edge_secret() -> str:
    """Read the shared edge secret, or "" when unset/unreadable.

    Unreadable is deliberately not fatal: it degrades to "record layer off",
    which property 1 already requires the caller to survive.
    """
    if not BITACORADB_EDGE_SECRET_PATH:
        return ""
    try:
        return Path(BITACORADB_EDGE_SECRET_PATH).read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("BITACORADB_EDGE_SECRET_PATH set but unreadable; record layer off")
        return ""


#: BitacoraDB's `NoteKind` enum is {observation, event, deviation, comment} —
#: narrower than the executor's vocabulary, which distinguishes a device fault
#: from a step that was sent and never answered. Translating at this boundary
#: keeps that distinction in the lab's code (where it is a real safety
#: difference) without inventing enum values the record layer would reject with
#: a 422 — which, since these are the notes for *failed* and *unknown* steps,
#: would silently lose exactly the two that matter.
#:
#: `outcome_unknown` deliberately does NOT become `deviation`: `notes_from` is
#: explicit that a note reading "deviation" invites someone to re-run a step
#: that may already have moved liquid. `event` is the neutral option available.
#: The precise executor status survives in `data.status` either way.
NOTE_KIND = {
    "device_fault": "deviation",
    "outcome_unknown": "event",
    "deviation": "deviation",
}


#: BitacoraDB's `PlanStatus`. `executing` is never used: the row is written
#: after the run ends, so it is already terminal by the time we can post it.
PLAN_STATUS = {"ok": "completed", "not_ok": "abandoned"}


def _terminal_status(meta: dict[str, Any]) -> str | None:
    """The status a finished run's Plan should carry, or None to leave it draft.

    `None` for a dry run — it is a preflight, not an execution, and marking it
    `completed` would put a run in the catalog that never touched hardware.
    A run that failed or was aborted did not complete, so it is `abandoned`;
    the *why* lives in the notes and `meta.ok`, which this never contradicts.
    """
    if meta.get("dry_run"):
        return None
    return PLAN_STATUS["ok"] if meta.get("ok") else PLAN_STATUS["not_ok"]


def note_for_record(note: dict[str, Any]) -> dict[str, Any]:
    """Translate one executor note into a `NoteCreate` the record layer accepts."""
    reported = note.get("kind", "")
    out = dict(note)
    out["kind"] = NOTE_KIND.get(reported, "event")
    if out["kind"] != reported:
        # Never lose the executor's own word for it.
        out["data"] = {**(note.get("data") or {}), "kind_reported": reported}
    return out


def _stem(path: str) -> str:
    """`designs/chanlam-v1.yaml` -> `chanlam-v1`."""
    return Path(path).stem if path else ""


def experiment_hid(*, design_ref: str | None, protocol_path: str) -> str:
    """Stable human id for the Experiment this run belongs to.

    `design_ref` first: the shared design *is* the campaign, so two plates of one
    design must land under one Experiment rather than inventing a second. Falling
    back to the protocol path keeps property 2 — a pre-shared-design protocol
    still gets an Experiment, and its notes still have somewhere to go.
    """
    return _stem(design_ref or "") or _stem(protocol_path) or "unfiled"


class RunRecorder:
    """The few record-layer calls the executor makes. One per write."""

    def __init__(self, base_url: str, secret: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret = secret
        self._timeout = timeout
        self._project = ""

    def _headers(self, user: str) -> dict[str, str]:
        # X-Auth-Projects is load-bearing, not decoration: BitacoraDB's
        # can_read row filter reduces a caller without it to an EMPTY scope, so
        # the ensure-GET reads [] even when the campaign's Experiment exists —
        # then the create 409s and the run's record is lost. The executor
        # asserts exactly the authorized run's own project (never a role, never
        # PI scope): membership was established when a human authorized the
        # run against that project. HERMES_ACCESS_DESIGN §2.1.
        h = {"X-Edge-Secret": self._secret, "X-Auth-User": user}
        if self._project:
            h["X-Auth-Projects"] = self._project
        return h

    async def _ensure_experiment(
        self, client: httpx.AsyncClient, *, project: str, hid: str,
        title: str, operator: str, started_at: str,
    ) -> str:
        """Return the Experiment id for `hid`, creating it if absent.

        Read-then-create rather than create-then-conflict because `POST
        /experiments` needs `started_at` and `operator`, which would otherwise be
        invented on every run and then discarded — the first run's values are the
        campaign's, and later runs must not overwrite them.
        """
        r = await client.get(f"{self._base_url}/experiments",
                             headers=self._headers(operator),
                             params={"project": project, "hid": hid})
        r.raise_for_status()
        rows = r.json()
        if rows:
            return str(rows[0]["experiment_id"])

        r = await client.post(f"{self._base_url}/experiments",
                              headers=self._headers(operator),
                              json={"hid": hid, "title": title, "project": project,
                                    "operator": operator, "creator": operator,
                                    "started_at": started_at,
                                    "meta": {"created_by": "ac-organic-lab/workflow"}})
        if r.status_code == 409:
            # Raced another run of the same campaign; re-read rather than fail.
            r = await client.get(f"{self._base_url}/experiments",
                                 headers=self._headers(operator),
                                 params={"project": project, "hid": hid})
            r.raise_for_status()
            rows = r.json()
            if not rows:
                # The 409 proves the row exists; an empty re-read means this
                # caller cannot SEE it — a scope/visibility gap, and indexing
                # the empty list would crash with a message that names neither.
                raise RuntimeError(
                    f"experiment {hid!r} exists (409) but is not readable "
                    f"with project scope {self._project!r} — can_read hides it")
            return str(rows[0]["experiment_id"])
        r.raise_for_status()
        return str(r.json()["experiment_id"])

    async def write(
        self, *, plan: dict[str, Any], notes: list[dict[str, Any]],
        design_ref: str | None, operator: str, started_at: str,
    ) -> dict[str, Any]:
        """File one run: ensure the Experiment, POST the Plan, POST each Note.

        Returns a status dict; never raises (property 1). Notes are attempted
        individually so one rejected note cannot cost the others.
        """
        project = plan.get("project") or ""
        self._project = project
        protocol_path = plan.get("protocol_path") or ""
        hid = experiment_hid(design_ref=design_ref, protocol_path=protocol_path)
        commit = (plan.get("source_commit") or "")[:7]
        title = f"{_stem(protocol_path) or 'run'}{f' @ {commit}' if commit else ''}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                experiment_id = await self._ensure_experiment(
                    client, project=project, hid=hid, title=hid,
                    operator=operator, started_at=started_at)

                r = await client.post(f"{self._base_url}/plans",
                                      headers=self._headers(operator),
                                      json={**plan, "title": title,
                                            "creator": operator,
                                            "experiment_id": experiment_id})
                r.raise_for_status()
                plan_id = str(r.json()["plan_id"])

                # A Plan is created `draft`. Left there, every filed run sits in
                # the catalog as a draft forever — the row says a run happened
                # while its status says one was never carried out.
                #
                # A *dry run* deliberately stays `draft`: it is a preflight, the
                # plan was never executed, and `completed` would claim otherwise.
                # `meta.dry_run` already records which it was.
                #
                # BitacoraDB admits no draft → completed edge: `completed` is
                # reachable only through the full lifecycle, so a successful run
                # walks draft → approved → executing → completed, while
                # `abandoned` is one legal hop from draft. The `approved` edge
                # stamps a sign-off, so it is posted as the human who authorized
                # the run in bitácora (meta.authorized_by) — never the launcher,
                # which for an agent-triggered run is the automation principal.
                # (The first live run, ra_67f32cb0920b4a41, filed as a permanent
                # draft because the single completed POST 409'd on this.)
                status = _terminal_status(plan.get("meta") or {})
                status_error = None
                if status:
                    walk = (["approved", "executing", "completed"]
                            if status == "completed" else [status])
                    approver = (plan.get("meta") or {}).get("authorized_by") or operator
                    for target in walk:
                        try:
                            rs = await client.post(
                                f"{self._base_url}/plans/{plan_id}/status",
                                headers=self._headers(
                                    approver if target == "approved" else operator),
                                json={"status": target})
                            rs.raise_for_status()
                        except Exception as exc:  # noqa: BLE001 — the row is already filed
                            status_error = f"{target}: {str(exc)[:180]}"
                            break

                written, failed = 0, []
                for note in notes:
                    try:
                        rn = await client.post(
                            f"{self._base_url}/notes",
                            headers=self._headers(operator),
                            json={**note_for_record(note),
                                  "experiment_id": experiment_id,
                                  "plan_id": plan_id, "creator": operator})
                        rn.raise_for_status()
                        written += 1
                    except Exception as exc:  # noqa: BLE001 — one note, not the run
                        failed.append({"step_id": note.get("step_id"), "error": str(exc)[:200]})

                return {"written": True, "experiment_id": experiment_id,
                        "plan_id": plan_id, "notes_written": written,
                        "notes_failed": failed or None,
                        "status": status or "draft",
                        "status_error": status_error}
        except Exception as exc:  # noqa: BLE001 — property 1
            logger.warning("record write failed for %s: %s", protocol_path, exc)
            return {"written": False, "error": str(exc)[:300],
                    "experiment_hid": hid, "notes_pending": len(notes)}


    # ── Plan-at-start (PLATE_TRACKING.md D9) ───────────────────────────────
    #
    # `write` files the Plan AFTER the run. Custody `move` rows are written
    # DURING it and anchor to `plan_id` + `step_id` on an append-only ledger, so
    # the Plan must exist before the first step: `open` posts it (planned
    # steps, `approved → executing`), `close` files the notes and the terminal
    # status. A Plan row cannot be edited afterwards (no PATCH), so the final
    # per-step statuses go into one summary Note — which is also the shape the
    # record layer's own semantics want: Plan = intent, Notes = what happened.
    # `write` stays as the fallback (dry runs, or a record layer that was down
    # at start) so a run is filed the old way rather than not at all.

    async def open(
        self, *, plan: dict[str, Any], design_ref: str | None,
        operator: str, started_at: str,
    ) -> dict[str, Any]:
        """Open the run's Plan before execution. Returns ``{"opened": True,
        experiment_id, plan_id}`` or ``{"opened": False, error}``; never raises."""
        project = plan.get("project") or ""
        self._project = project
        protocol_path = plan.get("protocol_path") or ""
        hid = experiment_hid(design_ref=design_ref, protocol_path=protocol_path)
        commit = (plan.get("source_commit") or "")[:7]
        title = f"{_stem(protocol_path) or 'run'}{f' @ {commit}' if commit else ''}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                experiment_id = await self._ensure_experiment(
                    client, project=project, hid=hid, title=hid,
                    operator=operator, started_at=started_at)
                r = await client.post(f"{self._base_url}/plans",
                                      headers=self._headers(operator),
                                      json={**plan, "title": title, "creator": operator,
                                            "experiment_id": experiment_id})
                r.raise_for_status()
                plan_id = str(r.json()["plan_id"])
                approver = (plan.get("meta") or {}).get("authorized_by") or operator
                status_error = None
                for target in ("approved", "executing"):
                    try:
                        rs = await client.post(
                            f"{self._base_url}/plans/{plan_id}/status",
                            headers=self._headers(approver if target == "approved" else operator),
                            json={"status": target})
                        rs.raise_for_status()
                    except Exception as exc:  # noqa: BLE001 — the row is filed; status is best-effort
                        status_error = f"{target}: {str(exc)[:180]}"
                        break
                return {"opened": True, "experiment_id": experiment_id, "plan_id": plan_id,
                        "status": "executing" if status_error is None else "draft",
                        "status_error": status_error}
        except Exception as exc:  # noqa: BLE001 — property 1
            logger.warning("record open failed for %s: %s", protocol_path, exc)
            return {"opened": False, "error": str(exc)[:300], "experiment_hid": hid}

    async def close(
        self, *, opened: dict[str, Any], plan: dict[str, Any],
        notes: list[dict[str, Any]], operator: str, summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Close an opened Plan: file the notes, one summary Note with the final
        per-step statuses, and the terminal status. Never raises."""
        self._project = plan.get("project") or ""
        experiment_id, plan_id = opened.get("experiment_id"), opened.get("plan_id")
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                written, failed = 0, []
                for note in [*notes, _summary_note(plan, summary)]:
                    try:
                        rn = await client.post(
                            f"{self._base_url}/notes",
                            headers=self._headers(operator),
                            json={**note_for_record(note), "experiment_id": experiment_id,
                                  "plan_id": plan_id, "creator": operator})
                        rn.raise_for_status()
                        written += 1
                    except Exception as exc:  # noqa: BLE001 — one note, not the run
                        failed.append({"step_id": note.get("step_id"), "error": str(exc)[:200]})
                status = _terminal_status(plan.get("meta") or {}) or "abandoned"
                status_error = None
                try:
                    rs = await client.post(f"{self._base_url}/plans/{plan_id}/status",
                                           headers=self._headers(operator),
                                           json={"status": status})
                    rs.raise_for_status()
                except Exception as exc:  # noqa: BLE001
                    status_error = f"{status}: {str(exc)[:180]}"
                return {"written": True, "experiment_id": experiment_id, "plan_id": plan_id,
                        "notes_written": written, "notes_failed": failed or None,
                        "status": status, "status_error": status_error, "opened_at_start": True}
        except Exception as exc:  # noqa: BLE001 — property 1
            logger.warning("record close failed for plan %s: %s", plan_id, exc)
            return {"written": False, "error": str(exc)[:300], "plan_id": plan_id,
                    "notes_pending": len(notes) + 1}


def _summary_note(plan: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    """The run's outcome as one step-less Note: final per-step statuses (the
    Plan row holds the *planned* steps and cannot be edited), `ok`, why it
    stopped, how long it took. `kind: event` — it is what happened, not a
    deviation; deviations have their own notes."""
    steps = plan.get("steps") or []
    counts: dict[str, int] = {}
    for s in steps:
        st = (s.get("params") or {}).get("status") or "?"
        counts[st] = counts.get(st, 0) + 1
    body = (f"run {'completed' if summary.get('ok') else 'did not complete'}: "
            + ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
            + (f"; stopped: {summary['aborted_reason']}" if summary.get("aborted_reason") else ""))
    return {"kind": "event", "body": body,
            "data": {"summary": True, "steps": steps, **summary}}


async def open_run_record(
    *, plan: dict[str, Any], design_ref: str | None, operator: str, started_at: str,
) -> dict[str, Any]:
    """Module-level entry point for :meth:`RunRecorder.open`; ``not_configured``
    when the record layer is off."""
    secret = edge_secret()
    if not BITACORADB_URL or not secret:
        return {"opened": False, "reason": "not_configured"}
    return await RunRecorder(BITACORADB_URL, secret).open(
        plan=plan, design_ref=design_ref, operator=operator, started_at=started_at)


async def close_run_record(
    *, opened: dict[str, Any], plan: dict[str, Any], notes: list[dict[str, Any]],
    design_ref: str | None, operator: str, started_at: str, summary: dict[str, Any],
) -> dict[str, Any]:
    """Close an opened run; when it was never opened (dry run, record layer down
    at start, unconfigured) fall back to :func:`write_run_record`, so the run is
    filed the old way rather than lost."""
    if not opened.get("opened"):
        out = await write_run_record(plan=plan, notes=notes, design_ref=design_ref,
                                     operator=operator, started_at=started_at)
        out["opened_at_start"] = False
        if opened.get("reason"):
            out.setdefault("open_reason", opened["reason"])
        return out
    secret = edge_secret()
    if not BITACORADB_URL or not secret:
        return {"written": False, "reason": "not_configured"}
    return await RunRecorder(BITACORADB_URL, secret).close(
        opened=opened, plan=plan, notes=notes, operator=operator, summary=summary)


async def write_run_record(
    *, plan: dict[str, Any], notes: list[dict[str, Any]],
    design_ref: str | None, operator: str, started_at: str,
) -> dict[str, Any]:
    """Module-level entry point. Reports `not_configured` when the record layer
    is off, which is a normal state and not an error."""
    secret = edge_secret()
    if not BITACORADB_URL or not secret:
        return {"written": False, "reason": "not_configured"}
    return await RunRecorder(BITACORADB_URL, secret).write(
        plan=plan, notes=notes, design_ref=design_ref,
        operator=operator, started_at=started_at)
