"""Custody — where each plate *is*, written by the executor and by humans,
never by devices (PLATE_TRACKING.md D5–D8).

Three things live here, deliberately in one module so the robot path and the
human path cannot drift:

* :class:`CustodyRecorder` — the few record-layer calls custody needs: resolve
  a location *name* (``ot2_hte/slot_2``) and a container *hid* (a plate's
  barcode, == the device's ``plate_id``) to their BitacoraDB ids, post one
  ``move`` row on the append-only ``ContainerAction`` ledger, and read a
  plate's current place back. Never raises into a run (record.py property 1):
  every call returns a status dict. It is also where the ledger's *other*
  executor-written verb lives — ``record_transfer`` / ``resolve_children``, the
  well-to-well rows :mod:`app.lineage` derives — because both are the same
  record-layer client and one client with two verbs cannot drift from itself.
* :func:`observe` / :func:`reconcile` — **pure** functions that turn a device's
  ``/status`` snapshot into an observation about the destination, and an
  observation into a verdict. A mismatch is declared **only on contradiction**
  (a device naming a *different* ``plate_id``, or a presence sensor reading
  empty); an absent signal is ``unobservable``, never a mismatch. Most devices
  can report presence at best, and ``details.loaded_plate`` is bookkeeping the
  orchestrator wrote, not a sensor — its absence proves nothing.
* The **human front door** — ``POST /api/custody/move``: a signed-in operator
  records a bench-top move (plate picked up by hand, put on a shelf). It writes
  the *same* ledger row the executor writes, with the human as
  ``performed_by`` and ``params.reason = "bench"``, audited as a
  ``control_action`` on pseudo-device ``custody`` and mirrored to lab.db as a
  ``plate_moved`` event. No local state anywhere — the ledger is the only
  truth, and lab.db rows are ops audit (LAB_MONITORING.md).

Aliases in ``locations.yaml`` are read-side only: they let :func:`observe`
map a device's own vocabulary (an OT-2 slot key, an xArm graph node) back to a
registry name. They are never used to *infer* a move — custody is declared on
the compiled step (bitácora's ``custody: {plate, hid, to}``) or by a human.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .record import BITACORADB_URL, edge_secret

logger = logging.getLogger("custody")

#: lab.db `event_type`s (LAB_MONITORING.md registry). Ops audit only — custody
#: itself is read from BitacoraDB, never from these rows.
PLATE_MOVED = "plate_moved"
PLATE_CUSTODY_MISMATCH = "plate_custody_mismatch"
PLATE_CUSTODY_UNKNOWN = "plate_custody_unknown"

#: The pseudo-device id the human front door audits under (like labware's
#: `labware_store`): there is no equipment behind a bench-top move.
CUSTODY_DEVICE_ID = "custody"

#: Run-start gate: when "1", a bound plate that the record layer cannot find
#: refuses the run instead of warning on the `started` frame (D7).
CUSTODY_STRICT = os.environ.get("CUSTODY_STRICT", "0") == "1"


# ── observation (pure) ───────────────────────────────────────────────────

ObservationKind = Literal["plate_id", "presence", "none"]
Verdict = Literal["match", "mismatch", "unobservable"]

#: Component states that mean "a plate is here" / "nothing is here", across
#: the fleet's vocabularies (PlateLoc `stage ∈ in|out`, press `plate ∈ in|out`,
#: doser `plate ∈ absent|present`, BioStack `handoff ∈ empty|…`, Cytation
#: `plate_stage`). Anything else is not a presence statement.
_PRESENT = {"in", "present", "loaded", "occupied", "holding", "plate_present"}
_ABSENT = {"out", "absent", "empty", "none", "no_plate"}
_PRESENCE_COMPONENTS = ("stage", "plate", "handoff", "plate_stage", "nest", "carrier")

#: OT-2 gateway `slot_state` → presence. `declared` is intent, `mismatch` is a
#: disagreement the device itself flagged — neither is an observation of ours.
_OT2_SLOT_PRESENT = {"occupied": True, "in_use": True, "empty": False}


@dataclass(frozen=True)
class Observation:
    """What a device snapshot says about the destination of a move."""

    kind: ObservationKind
    value: Any = None
    source: str = ""  # "<equipment_id>:<path>" or why nothing could be read

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value, "source": self.source}


def observe(snapshot: Any, location: Any, locations: Any = None) -> Observation:
    """Read what the device anchoring ``location`` says about that place.

    ``snapshot`` is the aggregator's ``EquipmentSnapshot`` (or ``None``);
    ``location`` the registry ``LocationEntry`` the plate was declared to
    arrive at; ``locations`` the ``LocationsConfig`` (for alias tokens). Pure:
    no I/O, no clock. Readers, in order of strength:

    1. ``details.loaded_plate.plate_id`` — the one place a device names the
       plate (Cytation; the OT-2's single tracked plate).
    2. OT-2 ``details.snapshot.deck.slots[<alias>]`` — ``labware.plate_id`` if
       present, else ``slot_state`` as presence.
    3. ``details.gripper.object_detected`` — for a gripper location.
    4. ``components[stage|plate|handoff|…].state`` — presence only.
    """
    if location is None or getattr(location, "equipment", None) is None:
        return Observation("none", None, "location has no equipment")
    eid = location.equipment
    if snapshot is None:
        return Observation("none", None, f"{eid}: no snapshot")
    if getattr(snapshot, "fetch_error", None):
        return Observation("none", None, f"{eid}: unreachable")
    status = getattr(snapshot, "status", None)
    if status is None:
        return Observation("none", None, f"{eid}: no status")
    details = getattr(status, "details", None) or {}
    components = getattr(status, "components", None) or {}

    # 2. OT-2 deck slot, keyed by the registry alias for this equipment.
    slots = ((details.get("snapshot") or {}).get("deck") or {}).get("slots")
    if isinstance(slots, dict) and locations is not None:
        for token in location.alias_tokens(eid):
            slot = slots.get(token)
            if not isinstance(slot, dict):
                continue
            labware = slot.get("labware") or {}
            if isinstance(labware, dict) and labware.get("plate_id"):
                return Observation("plate_id", labware["plate_id"],
                                   f"{eid}:details.snapshot.deck.slots[{token}].labware.plate_id")
            present = _OT2_SLOT_PRESENT.get(slot.get("slot_state"))
            if present is not None:
                return Observation("presence", present,
                                   f"{eid}:details.snapshot.deck.slots[{token}].slot_state")
    # 1. A device that names its plate.
    loaded = details.get("loaded_plate")
    if isinstance(loaded, dict) and loaded.get("plate_id"):
        return Observation("plate_id", loaded["plate_id"], f"{eid}:details.loaded_plate.plate_id")
    # 3. Gripper.
    if location.name.endswith("/gripper"):
        gripper = details.get("gripper")
        if isinstance(gripper, dict) and "object_detected" in gripper:
            return Observation("presence", bool(gripper["object_detected"]),
                               f"{eid}:details.gripper.object_detected")
    # 4. Presence components.
    for name in _PRESENCE_COMPONENTS:
        comp = components.get(name)
        state = getattr(comp, "state", None) if comp is not None else None
        if state is None and isinstance(comp, dict):
            state = comp.get("state")
        if not isinstance(state, str):
            continue
        s = state.lower()
        if s in _PRESENT:
            return Observation("presence", True, f"{eid}:components.{name}.state={state}")
        if s in _ABSENT:
            return Observation("presence", False, f"{eid}:components.{name}.state={state}")
    return Observation("none", None, f"{eid}: no occupancy signal")


def reconcile(expected_hid: str, observation: Observation) -> Verdict:
    """Commanded vs observed. **Mismatch only on contradiction.**

    A named plate_id that differs → mismatch; the same → match. A presence
    sensor reading empty right after a place → mismatch; reading present →
    match (it cannot tell *which* plate, so this is the weak match it is).
    No signal → unobservable, which is not evidence of anything.
    """
    if observation.kind == "plate_id":
        if not observation.value:
            return "unobservable"
        return "match" if str(observation.value) == expected_hid else "mismatch"
    if observation.kind == "presence":
        return "match" if observation.value else "mismatch"
    return "unobservable"


# ── the recorder (record-layer client; never raises) ─────────────────────


@dataclass
class CustodyRecorder:
    """The few record-layer calls custody needs. One instance per run (or per
    request); caches name/hid resolutions for its lifetime."""

    base_url: str
    secret: str
    timeout: float = 10.0
    _locations: dict[str, str] = field(default_factory=dict)   # name → location_id
    _containers: dict[str, dict] = field(default_factory=dict)  # hid → row
    #: hid → {position → container_id} for a plate's positional children. One
    #: GET per plate per run: a 96-well transfer would otherwise ask the same
    #: question 96 times, and a plate's wells do not appear or vanish mid-run.
    _children: dict[str, dict[str, str]] = field(default_factory=dict)

    def _headers(self, user: str, project: str | None = None) -> dict[str, str]:
        h = {"X-Edge-Secret": self.secret, "X-Auth-User": user}
        if project:
            # The executor asserts the authorized run's own project, exactly as
            # RunRecorder does; lab-scoped rows need any non-empty scope.
            h["X-Auth-Projects"] = project
        return h

    async def resolve_location(self, client: httpx.AsyncClient, name: str, *,
                               user: str, project: str | None = None) -> str | None:
        if name in self._locations:
            return self._locations[name]
        r = await client.get(f"{self.base_url}/locations",
                             headers=self._headers(user, project), params={"name": name})
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        self._locations[name] = str(rows[0]["location_id"])
        return self._locations[name]

    async def resolve_container(self, client: httpx.AsyncClient, hid: str, *,
                                user: str, project: str | None = None,
                                refresh: bool = False) -> dict | None:
        """The container row for ``hid``, cached for this recorder's lifetime.

        ``refresh`` re-reads it. The cache is safe for what ``record_move``
        needs — a container's id and identity never change — but a row also
        carries ``location_id``, which is *precisely* what a move changes. A
        caller asking where a plate is now must say so, or it gets the answer
        from before this run started moving it.
        """
        if hid in self._containers and not refresh:
            return self._containers[hid]
        r = await client.get(f"{self.base_url}/containers",
                             headers=self._headers(user, project), params={"hid": hid})
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        self._containers[hid] = rows[0]
        return rows[0]

    async def resolve_children(self, client: httpx.AsyncClient, hid: str, *,
                               user: str, project: str | None = None) -> dict[str, str] | None:
        """``{position: container_id}`` for the plate ``hid``'s wells, cached.

        A plate is one container with 96 positional children
        (``UNIQUE(parent_container_id, position)``), and a `transfer` row points
        at the *wells*, not the plate — so lineage needs this join and custody
        never did. ``None`` means the plate itself is unknown to the ledger; an
        empty dict means it is registered without children, which is a real and
        different state (a plate minted before ``ContainerCreate.positions``
        existed, or a container that is genuinely not a plate).
        """
        if hid in self._children:
            return self._children[hid]
        row = await self.resolve_container(client, hid, user=user, project=project)
        if row is None:
            return None
        r = await client.get(f"{self.base_url}/containers",
                             headers=self._headers(user, project),
                             params={"parent_container_id": row["container_id"]})
        r.raise_for_status()
        wells = {str(c["position"]): str(c["container_id"])
                 for c in r.json() if c.get("position")}
        self._children[hid] = wells
        return wells

    async def record_transfer(
        self, *, source_hid: str, source_well: str | None,
        dest_hid: str, dest_well: str | None, performed_by: str, recorder: str,
        amount_commanded: float | None = None, unit: str | None = None,
        project: str | None = None, plan_id: str | None = None,
        step_id: str | None = None, params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One ``transfer`` row: the contents of one well fed another
        (PLATE_TRACKING.md D11).

        Source and target are the **child** (well) containers, which is what
        makes this row lineage rather than another custody move: `move` says a
        plate changed place, `transfer` says a well's contents have a parent.

        ``amount_commanded`` + ``unit`` (a UCUM code from the ledger's ``Unit``
        enum) travel together or not at all, and both are omitted from the body
        when the caller has no amount — :mod:`app.lineage` decides that, and an
        omitted column is how the ledger says "unknown". ``amount_observed``
        has no writer yet: no device reports what it actually poured.

        Returns ``{"recorded": True, action_id, source_container_id,
        target_container_id}`` or ``{"recorded": False, "reason": …}``; never
        raises, and only sends ``step_id`` together with a ``plan_id`` (the
        ledger refuses a dangling step), exactly like :meth:`record_move`.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                ends = {}
                for side, hid, well in (("source", source_hid, source_well),
                                        ("dest", dest_hid, dest_well)):
                    wells = await self.resolve_children(client, hid, user=recorder,
                                                        project=project)
                    if wells is None:
                        return {"recorded": False, "reason": "unknown_container",
                                "side": side, "hid": hid}
                    if not wells:
                        return {"recorded": False, "reason": "no_child_containers",
                                "side": side, "hid": hid}
                    if well not in wells:
                        return {"recorded": False, "reason": "unknown_well",
                                "side": side, "hid": hid, "well": well}
                    ends[side] = wells[well]
                body: dict[str, Any] = {
                    "action_type": "transfer",
                    "source_container_id": ends["source"],
                    "target_container_id": ends["dest"],
                    "performed_by": performed_by,
                    "creator": recorder,
                    "params": {**(params or {}),
                               "source": {"hid": source_hid, "well": source_well},
                               "dest": {"hid": dest_hid, "well": dest_well}},
                }
                if amount_commanded is not None and unit:
                    body["amount_commanded"] = amount_commanded
                    body["unit"] = unit
                if plan_id:
                    body["plan_id"] = plan_id
                    if step_id:
                        body["step_id"] = step_id
                elif step_id:
                    body["params"]["step_id"] = step_id  # no plan to anchor into
                if project:
                    body["project"] = project
                r = await client.post(f"{self.base_url}/container-actions",
                                      headers=self._headers(recorder, project), json=body)
                if r.status_code >= 400:
                    return {"recorded": False, "reason": f"http_{r.status_code}",
                            "detail": r.text[:300]}
                out = r.json()
                return {"recorded": True, "action_id": out.get("action_id"),
                        "source_container_id": ends["source"],
                        "target_container_id": ends["dest"]}
        except Exception as exc:  # noqa: BLE001 — property 1
            logger.warning("transfer not recorded (%s:%s → %s:%s): %s",
                           source_hid, source_well, dest_hid, dest_well, exc)
            return {"recorded": False, "reason": "unreachable", "detail": str(exc)[:300]}

    async def record_move(
        self, *, hid: str, to: str, performed_by: str, recorder: str,
        project: str | None = None, plan_id: str | None = None,
        step_id: str | None = None, observed: Observation | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One ``move`` row: container ``hid`` is now at place ``to``.

        Returns ``{"recorded": True, action_id, container_id, to_location_id}``
        or ``{"recorded": False, "reason": …}``; never raises. ``step_id`` is
        only sent with a ``plan_id`` (the ledger refuses a dangling step).
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                row = await self.resolve_container(client, hid, user=recorder, project=project)
                if row is None:
                    return {"recorded": False, "reason": "unknown_container", "hid": hid}
                location_id = await self.resolve_location(client, to, user=recorder, project=project)
                if location_id is None:
                    return {"recorded": False, "reason": "unknown_location", "to": to}
                body: dict[str, Any] = {
                    "action_type": "move",
                    "target_container_id": row["container_id"],
                    "to_location_id": location_id,
                    "performed_by": performed_by,
                    "creator": recorder,
                    "params": {**(params or {}),
                               "observed": observed.as_dict() if observed else None},
                }
                if plan_id:
                    body["plan_id"] = plan_id
                    if step_id:
                        body["step_id"] = step_id
                elif step_id:
                    body["params"]["step_id"] = step_id  # no plan to anchor into (yet)
                if project:
                    body["project"] = project
                r = await client.post(f"{self.base_url}/container-actions",
                                      headers=self._headers(recorder, project), json=body)
                if r.status_code >= 400:
                    return {"recorded": False, "reason": f"http_{r.status_code}",
                            "detail": r.text[:300]}
                out = r.json()
                return {"recorded": True, "action_id": out.get("action_id"),
                        "container_id": row["container_id"], "to_location_id": location_id}
        except Exception as exc:  # noqa: BLE001 — property 1
            logger.warning("custody move not recorded (%s → %s): %s", hid, to, exc)
            return {"recorded": False, "reason": "unreachable", "detail": str(exc)[:300]}

    async def current_location(self, hid: str, *, user: str,
                               project: str | None = None,
                               refresh: bool = False) -> dict[str, Any]:
        """``{"found": bool, "hid", "container_id", "location_id", "location_name"}``;
        never raises (``found: None`` when the store could not answer).

        Pass ``refresh=True`` for a *fresh* answer — a recorder that has already
        written a move for this plate holds the pre-move row (see
        :meth:`resolve_container`), and comparing against that would report the
        plate as still where it was before the run touched it.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                row = await self.resolve_container(client, hid, user=user,
                                                   project=project, refresh=refresh)
                if row is None:
                    return {"found": False, "hid": hid}
                name = None
                if row.get("location_id"):
                    r = await client.get(f"{self.base_url}/locations/{row['location_id']}",
                                         headers=self._headers(user, project))
                    if r.status_code == 200:
                        name = r.json().get("name")
                return {"found": True, "hid": hid, "container_id": row["container_id"],
                        "location_id": row.get("location_id"), "location_name": name,
                        "status": row.get("status")}
        except Exception as exc:  # noqa: BLE001
            return {"found": None, "hid": hid, "error": str(exc)[:200]}

    async def plates(self, *, user: str, projects: str = "", hid: str | None = None) -> list[dict]:
        """Top-level containers (no parent) joined with their location name.
        Raises on transport failure — a read endpoint should say so, not
        render an empty lab."""
        headers = {"X-Edge-Secret": self.secret, "X-Auth-User": user}
        if projects:
            headers["X-Auth-Projects"] = projects
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            params = {"hid": hid} if hid else {}
            rc = await client.get(f"{self.base_url}/containers", headers=headers, params=params)
            rc.raise_for_status()
            rl = await client.get(f"{self.base_url}/locations", headers=headers)
            rl.raise_for_status()
        names = {l["location_id"]: l for l in rl.json()}
        out = []
        for c in rc.json():
            if c.get("parent_container_id"):
                continue
            loc = names.get(c.get("location_id") or "")
            out.append({
                "hid": c["hid"], "container_id": c["container_id"],
                "container_type": c.get("container_type"), "model": c.get("model"),
                "status": c.get("status"), "location_id": c.get("location_id"),
                "location": loc["name"] if loc else None,
                "equipment_id": loc.get("equipment_id") if loc else None,
                "project_id": c.get("project_id"),
            })
        return out

    async def history(self, container_id: str, *, user: str, projects: str = "") -> list[dict]:
        headers = {"X-Edge-Secret": self.secret, "X-Auth-User": user}
        if projects:
            headers["X-Auth-Projects"] = projects
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(f"{self.base_url}/container-actions", headers=headers,
                                 params={"container_id": container_id})
            r.raise_for_status()
            return r.json()


def custody_recorder() -> CustodyRecorder | None:
    """A recorder for the configured record layer, or ``None`` when off —
    the same switch as ``record.write_run_record`` (property 3)."""
    secret = edge_secret()
    if not BITACORADB_URL or not secret:
        return None
    return CustodyRecorder(BITACORADB_URL, secret)


# ── lab.db mirror (ops audit) ────────────────────────────────────────────


async def record_custody_event(request: Request, event_type: str, *, device_id: str,
                               message: str, payload: dict[str, Any]) -> None:
    """Best-effort lab.db row; never raises (control.py's audit discipline)."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        return
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, functools.partial(db.record_equipment_event, device_id, event_type,
                                    message=message, payload=payload))
    except Exception as exc:  # noqa: BLE001
        logger.warning("custody audit write failed (%s): %s", event_type, exc)


# ── the human front door ─────────────────────────────────────────────────


class MoveRequest(BaseModel):
    hid: str = Field(min_length=1, description="The plate's barcode / Container.hid")
    to: str = Field(min_length=1, description="Registry location name, e.g. bench/hte_staging")
    note: str | None = Field(default=None, max_length=500)
    #: Who physically did it, when not the signed-in user (default: the user).
    performed_by: str | None = Field(default=None, max_length=128)


def _signed_in(request: Request) -> str:
    """Same gate as labware.py: a verified identity, or the generic dashboard
    owner when the deployment runs open (CONTROL_AUTHZ_ENFORCE=false / dev)."""
    from .labware import _DASHBOARD_OWNER, _authz_enforced

    user = request.headers.get("x-auth-user")
    if not _authz_enforced() or not user:
        return user or _DASHBOARD_OWNER
    return user


def build_custody_router() -> APIRouter:
    router = APIRouter(prefix="/api/custody", tags=["custody"])

    @router.post("/move")
    async def move(body: MoveRequest, request: Request) -> dict:
        """Record a bench-top move: container ``hid`` is now at ``to``.

        The same ledger row the executor writes for a robot move, with the
        human as ``performed_by``. The registry name is checked locally first
        (a typo must not reach the ledger), then resolved in the record layer.
        """
        user = _signed_in(request)
        recorder = custody_recorder()
        if recorder is None:
            raise HTTPException(status_code=503, detail="record layer not configured — custody cannot be recorded")
        cfg = getattr(request.app.state, "locations_config", None)
        if cfg is not None:
            entry = cfg.by_name(body.to)
            if entry is None or not entry.active:
                raise HTTPException(status_code=422, detail=f"{body.to!r} is not an active place in locations.yaml")
        projects = request.headers.get("x-auth-projects", "")
        result = await recorder.record_move(
            hid=body.hid, to=body.to, performed_by=body.performed_by or user,
            recorder=user, project=projects.split(",")[0].strip() or None,
            params={"reason": "bench", "note": body.note, "via": "dashboard"},
        )
        outcome = "ok" if result.get("recorded") else result.get("reason", "failed")
        await record_custody_event(
            request, "control_action", device_id=CUSTODY_DEVICE_ID,
            message=f"{user} move {body.hid} → {body.to} → {outcome}",
            payload={"action": "custody.move", "method": "POST", "owner": user,
                     "outcome": outcome, "detail": {"hid": body.hid, "to": body.to}},
        )
        if result.get("recorded"):
            await record_custody_event(
                request, PLATE_MOVED, device_id=CUSTODY_DEVICE_ID,
                message=f"{body.hid} → {body.to} (by {body.performed_by or user})",
                payload={"hid": body.hid, "to": body.to, "performed_by": body.performed_by or user,
                         "recorded_by": user, "source": "bench",
                         "action_id": result.get("action_id")},
            )
            return {"recorded": True, "hid": body.hid, "to": body.to, **result}
        reason = result.get("reason")
        if reason == "unknown_container":
            raise HTTPException(status_code=404, detail=f"no container with hid {body.hid!r} is registered")
        if reason == "unknown_location":
            raise HTTPException(status_code=422, detail=f"{body.to!r} is not seeded in the record layer — run scripts/seed_locations.py")
        raise HTTPException(status_code=502, detail=f"record layer refused the move: {result}")

    @router.get("/plates")
    async def plates(request: Request, hid: str | None = None) -> dict:
        """Where every plate is — a read-through to the record layer (no cache)."""
        recorder = custody_recorder()
        if recorder is None:
            raise HTTPException(status_code=503, detail="record layer not configured")
        user = _signed_in(request)
        try:
            rows = await recorder.plates(user=user, projects=request.headers.get("x-auth-projects", ""), hid=hid)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"record layer unreachable: {exc}") from exc
        return {"plates": rows}

    @router.get("/plates/{hid}")
    async def plate(hid: str, request: Request) -> dict:
        recorder = custody_recorder()
        if recorder is None:
            raise HTTPException(status_code=503, detail="record layer not configured")
        user = _signed_in(request)
        projects = request.headers.get("x-auth-projects", "")
        try:
            rows = await recorder.plates(user=user, projects=projects, hid=hid)
            if not rows:
                raise HTTPException(status_code=404, detail=f"no container with hid {hid!r}")
            history = await recorder.history(rows[0]["container_id"], user=user, projects=projects)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"record layer unreachable: {exc}") from exc
        return {**rows[0], "history": history}

    return router
