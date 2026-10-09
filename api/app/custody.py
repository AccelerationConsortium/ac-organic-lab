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
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

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
        # Cytation service.py publishes self._drawer here, not a presence
        # sensor. An open carrier can contain the human-placed plate; a closed
        # carrier can be empty. Named loaded_plate contradictions still apply.
        if name == "plate_stage" and (eid == "cytation_5" or
                getattr(status, "equipment_kind", None) == "plate_reader"):
            continue
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


_UNSET_LOCATION = object()


# ── placement: the resolved place, not the raw cache ─────────────────────
#
# Since BitacoraDB contract 0.16.0 a container may be *seated* on another
# container (a vial in rack slot B3, a filter plate on its collector) instead
# of being at a place, and its place is derived by the record layer
# (`resolved_location_id`, the walk up the seating chain). Every reader here
# asks for that resolved value and falls back to the raw `location_id` only
# when the row predates 0.16.0 — a seated plate must never read as unlocated.
# PLATE_TRACKING.md §11.3 "Where is X" / BitacoraDB docs/ADAPTERS_AND_SEATING.md.

SEATING_CONTRACT = (0, 16, 0)


def resolved_location_id(row: dict) -> str | None:
    """The place a container is actually at, per the record layer."""
    if "resolved_location_id" in row:
        value = row["resolved_location_id"]
        return str(value) if value else None
    value = row.get("location_id")
    return str(value) if value else None


def seat_of(row: dict) -> dict | None:
    """``{container_id, hid, site, readable}`` when the row is seated, else None.

    A carrier the caller may not read arrives masked (`container_id` null,
    `readable: false` on the chain's first hop); the seat is still a seat.
    """
    chain = row.get("seating_chain") or []
    site = row.get("seated_at_site")
    carrier = row.get("seated_on_container_id")
    if not chain and carrier is None:
        return None
    first = chain[0] if chain else {}
    return {
        "container_id": str(carrier) if carrier else (first.get("container_id") or None),
        "hid": first.get("hid"),
        "site": site or first.get("site"),
        "readable": bool(first.get("readable", carrier is not None)),
    }


def expected_placement_guard(row: dict) -> dict[str, Any]:
    """The stale-state guard for moving ``row`` from where the ledger last
    said it was: the seat when it is seated, the place otherwise. Sending the
    raw ``location_id`` (null) for a seated container would assert "unlocated
    and unseated" and be refused — correctly, but for the wrong reason."""
    seat = seat_of(row)
    if seat is not None:
        return {"expected_seated_on_container_id": seat["container_id"],
                "expected_seated_at_site": seat["site"]}
    return {"expected_location_id": row.get("location_id")}


#: The ledger's shape rule for a site name (``bitacoradb.schemas.containers.
#: valid_site``), mirrored so a malformed site is refused here, before a write.
_SITE_MAX = 32


def valid_site(value: str) -> bool:
    return bool(value) and len(value) <= _SITE_MAX and "/" not in value and " " not in value


def site_manifest(row: dict) -> list[str] | None:
    """A container's declared sites (``meta.sites``), or ``None`` when it has
    no manifest — then the ledger accepts any shape-valid site and says so."""
    meta = row.get("meta") or {}
    sites = meta.get("sites")
    return [str(x) for x in sites] if isinstance(sites, list) else None


@dataclass(frozen=True)
class Seat:
    """The other kind of destination (contract 0.16.0): a carrier, by hid,
    and which of its sites — ``RK-003 @ B3``. The container's place is then
    the carrier's, wherever that goes."""

    adapter_hid: str
    site: str

    @property
    def text(self) -> str:
        return f"{self.adapter_hid} @ {self.site}"


# ── registration: what a container is, from the labware definition ────────
#
# The ledger is definition-free (§11.3): a plate's wells are minted from the
# `positions` the registering client sends, and an adapter's site manifest is
# whatever `meta.sites` it sends. This stack owns the definitions
# (`api/app/labware.py`: repo-committed, builder uploads, the Opentrons
# standard set), so the register front door derives both from the `model`.

#: ``bitacoradb.models.enums.ContainerType`` — mirrored so a typo is a 422
#: here. ``well`` is minted by the ledger, never registered by a client.
CONTAINER_TYPES = frozenset({
    "plate", "vial", "bottle", "flask", "filter_plate", "reservoir", "tiprack",
    "rack", "adapter", "other",
})
#: Site-providing, material-free carriers: a manifest, never positional children.
ADAPTER_TYPES = frozenset({"rack", "adapter"})
#: Types whose addresses are positional children (wells), minted at registration.
WELL_BEARING_TYPES = frozenset({"plate", "filter_plate", "reservoir"})
#: Opentrons ``metadata.displayCategory`` → ledger ``container_type`` (the
#: mapping ``ContainerType``'s docstring states; ``aluminumBlock`` is an adapter).
CATEGORY_TYPES = {
    "wellPlate": "plate", "reservoir": "reservoir", "tipRack": "tiprack",
    "tubeRack": "rack", "adapter": "adapter", "aluminumBlock": "adapter",
}
#: The standard grids the ``wells`` shorthand accepts (the same table bitácora's
#: ``propose_plate_registration`` uses), for a plate whose definition is not in the store.
WELL_LAYOUTS = {6: (2, 3), 12: (3, 4), 24: (4, 6), 48: (6, 8), 96: (8, 12), 384: (16, 24)}

_ADDRESS_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


def _address_key(name: str) -> tuple:
    m = _ADDRESS_RE.match(name)
    return (len(m.group(1)), m.group(1).upper(), int(m.group(2))) if m else (99, name, 0)


def grid_positions(wells: int) -> list[str]:
    """``A1 … H12`` row-major for a standard grid (``WELL_LAYOUTS``)."""
    rows, cols = WELL_LAYOUTS[wells]
    return [f"{chr(ord('A') + r)}{c + 1}" for r in range(rows) for c in range(cols)]


def labware_layout(definition: dict, *, source: str | None = None) -> dict[str, Any]:
    """What a schema-2 labware definition says a container is:
    ``{"container_type", "category", "addresses", "definition": {load_name,
    namespace, version, source}}``. ``addresses`` is every well/site name in
    ``ordering`` (Opentrons stores it column-major), sorted row-major so it
    reads like a plate map. A definition with no wells (a tiprack adapter)
    has ``addresses == []``."""
    meta = definition.get("metadata") or {}
    params = definition.get("parameters") or {}
    ordering = definition.get("ordering") or []
    addresses = sorted({str(w) for col in ordering if isinstance(col, list) for w in col}, key=_address_key)
    category = str(meta.get("displayCategory") or "wellPlate")
    return {
        "container_type": CATEGORY_TYPES.get(category, "other"),
        "category": category,
        "addresses": addresses,
        "definition": {"load_name": params.get("loadName"), "namespace": definition.get("namespace"),
                       "version": definition.get("version"), "source": source},
    }


def _parse_version(text: Any) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(text).split("."))
    except ValueError:
        return ()


_CONTRACT_CACHE: dict[str, tuple[tuple[int, ...], float]] = {}
_CONTRACT_TTL_S = 60.0


async def record_layer_contract(client: httpx.AsyncClient, base_url: str) -> tuple[int, ...]:
    """The record layer's live contract version (``/status`` →
    ``details.schema_version``), cached briefly. ``()`` when unknown — the
    caller then asks only for what every version answers."""
    now = time.monotonic()
    hit = _CONTRACT_CACHE.get(base_url)
    if hit and now - hit[1] < _CONTRACT_TTL_S:
        return hit[0]
    version: tuple[int, ...] = ()
    try:
        r = await client.get(f"{base_url}/status")
        if r.status_code == 200:
            version = _parse_version((r.json().get("details") or {}).get("schema_version"))
    except httpx.HTTPError:
        version = ()
    _CONTRACT_CACHE[base_url] = (version, now)
    return version

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

    async def _seat_target(self, client: httpx.AsyncClient, seat: Seat, *, hid: str,
                           user: str, project: str | None) -> dict[str, Any]:
        """Resolve a seat to ``{"container_id", "site"}`` of the carrier, or
        ``{"reason": …}`` when it must not reach the ledger: the container
        would sit in itself, the site is malformed, the live record layer
        predates seating (contract < 0.16.0 rejects the fields), the carrier
        is unknown, or the site is not in the carrier's manifest. Occupancy
        is not checked (D2)."""
        if seat.adapter_hid == hid:
            return {"reason": "self_seat", "hid": hid}
        if not valid_site(seat.site):
            return {"reason": "invalid_site", "site": seat.site}
        contract = await record_layer_contract(client, self.base_url)
        if contract < SEATING_CONTRACT:
            return {"reason": "seating_unsupported", "contract": ".".join(map(str, contract)) or None}
        carrier = await self.resolve_container(client, seat.adapter_hid, user=user, project=project)
        if carrier is None:
            return {"reason": "unknown_adapter", "adapter_hid": seat.adapter_hid}
        manifest = site_manifest(carrier)
        if manifest is not None and seat.site not in manifest:
            return {"reason": "unknown_site", "adapter_hid": seat.adapter_hid,
                    "site": seat.site, "sites": manifest}
        return {"container_id": carrier["container_id"], "site": seat.site}

    async def register_container(
        self, *, hid: str, container_type: str, recorder: str, project: str | None = None,
        owner_project: str | None = None, model: str | None = None, status: str = "empty",
        positions: list[str] | None = None, sites: list[str] | None = None,
        meta: dict[str, Any] | None = None, at: str | None = None, seat: Seat | None = None,
    ) -> dict[str, Any]:
        """``POST /containers``: one new container (a plate with its wells via
        ``positions``; an adapter with its manifest via ``sites``), optionally
        received *at* place ``at`` or *in* ``seat`` in the same transaction —
        the only way a new container gets a placement.

        ``project`` is the caller's scope (header); ``owner_project`` makes the
        row project-private (``None`` = lab-scoped, the normal case for
        labware). Returns ``{"registered": True, container_id, hid, …}`` or
        ``{"registered": False, "reason": …}``; never raises. A hid is unique
        for all time, so a taken hid is refused here before the write; a lost
        response is reported ``uncertain`` — the next attempt sees the hid
        taken and the map shows the row, which is the truthful recovery.
        """
        if at is not None and seat is not None:
            raise ValueError("register_container receives at a place (at=) or in a seat (seat=), not both")
        write_started = False
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                existing = await self.resolve_container(client, hid, user=recorder, project=project, refresh=True)
                if existing is not None:
                    return {"registered": False, "reason": "hid_taken", "hid": hid,
                            "container_id": existing["container_id"]}
                body: dict[str, Any] = {
                    "hid": hid, "container_type": container_type, "model": model, "status": status,
                    "creator": recorder,
                    "meta": {**(meta or {}), **({"sites": list(sites)} if sites is not None else {})},
                }
                if positions:
                    body["positions"] = list(positions)
                if owner_project:
                    body["project"] = owner_project
                if at is not None:
                    location_id = await self.resolve_location(client, at, user=recorder, project=project)
                    if location_id is None:
                        return {"registered": False, "reason": "unknown_location", "to": at}
                    body["received_at_location_id"] = location_id
                elif seat is not None:
                    target = await self._seat_target(client, seat, hid=hid, user=recorder, project=project)
                    if "reason" in target:
                        return {"registered": False, **target}
                    body["received_at_container_id"] = target["container_id"]
                    body["received_at_site"] = target["site"]
                write_started = True
                r = await client.post(f"{self.base_url}/containers",
                                      headers=self._headers(recorder, project), json=body)
                if r.status_code >= 400:
                    return {"registered": False, "reason": f"http_{r.status_code}",
                            "detail": r.text[:300], "uncertain": r.status_code >= 500}
                out = r.json()
                if not out.get("container_id"):
                    raise ValueError("record layer returned no container_id")
                self._containers.pop(hid, None)
                return {"registered": True, "container_id": out["container_id"], "hid": hid,
                        "container_type": container_type, "positions": len(body.get("positions") or []),
                        "sites": sites, "resolved_location_id": out.get("resolved_location_id"),
                        "received_at_location_id": body.get("received_at_location_id"),
                        "received_at_container_id": body.get("received_at_container_id"),
                        "received_at_site": body.get("received_at_site")}
        except Exception as exc:  # noqa: BLE001 — property 1
            logger.warning("custody registration not recorded (%s): %s", hid, exc)
            return {"registered": False, "reason": "unreachable", "detail": str(exc)[:300],
                    "uncertain": write_started}

    async def record_move(
        self, *, hid: str, to: str | None = None, seat: Seat | None = None,
        performed_by: str, recorder: str,
        project: str | None = None, plan_id: str | None = None,
        step_id: str | None = None, observed: Observation | None = None,
        params: dict[str, Any] | None = None,
        client_action_id: str | None = None, expected_from: str | None | object = _UNSET_LOCATION,
    ) -> dict[str, Any]:
        """One ``move`` row: container ``hid`` is now at place ``to``, or in
        ``seat`` (a carrier's site) — exactly one of the two.

        Returns ``{"recorded": True, action_id, container_id, to_location_id}``
        (or ``to_container_id`` + ``to_site`` for a seat) or
        ``{"recorded": False, "reason": …}``; never raises once the call is
        well-formed. ``step_id`` is only sent with a ``plan_id`` (the ledger
        refuses a dangling step).

        A seat is checked here before anything reaches the ledger: the carrier
        must be registered, the site must be in its manifest when it has one,
        and the live record layer must speak contract 0.16.0 (an older one
        rejects the unknown fields). Occupancy is NOT checked — the ledger
        records a double booking and flags it (D2); refusing would make the
        ledger lie about where a hand put a vial.
        """
        if (to is None) == (seat is None):
            raise ValueError("record_move takes a place (to=) or a seat (seat=), exactly one")
        destination = to if to is not None else seat.text  # type: ignore[union-attr]
        write_started = False
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                row = await self.resolve_container(client, hid, user=recorder, project=project,
                                                   refresh=expected_from is _UNSET_LOCATION)
                if row is None:
                    return {"recorded": False, "reason": "unknown_container", "hid": hid}
                dest: dict[str, Any]
                if seat is not None:
                    target = await self._seat_target(client, seat, hid=hid, user=recorder, project=project)
                    if "reason" in target:
                        return {"recorded": False, **target}
                    dest = {"to_container_id": target["container_id"], "to_site": target["site"]}
                else:
                    location_id = await self.resolve_location(client, to, user=recorder, project=project)  # type: ignore[arg-type]
                    if location_id is None:
                        return {"recorded": False, "reason": "unknown_location", "to": to}
                    dest = {"to_location_id": location_id}
                if expected_from is _UNSET_LOCATION:
                    # Where the ledger last said it was — a seat when seated.
                    guard = expected_placement_guard(row)
                elif expected_from is None:
                    guard = {"expected_location_id": None}
                else:
                    expected_id = await self.resolve_location(client, expected_from,
                                                              user=recorder, project=project)
                    if expected_id is None:
                        return {"recorded": False, "reason": "unknown_expected_location"}
                    guard = {"expected_location_id": expected_id}
                from uuid import uuid4
                body: dict[str, Any] = {
                    "client_action_id": client_action_id or f"dashboard:bench:{uuid4().hex}",
                    **guard,
                    "action_type": "move",
                    "target_container_id": row["container_id"],
                    **dest,
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
                # Only the ledger POST is retried, never the physical step.
                # Both attempts send identical ids, expected state and payload.
                write_started = True
                for attempt in range(2):
                    try:
                        r = await client.post(f"{self.base_url}/container-actions",
                                              headers=self._headers(recorder, project), json=body)
                    except httpx.TransportError:
                        if attempt:
                            raise
                        continue
                    if r.status_code < 500 or attempt:
                        break
                if r.status_code >= 400:
                    return {"recorded": False, "reason": f"http_{r.status_code}",
                            "detail": r.text[:300], "uncertain": r.status_code >= 500}
                out = r.json()
                if not out.get("action_id"):
                    raise ValueError("record layer returned no action_id")
                return {"recorded": True, "action_id": out.get("action_id"),
                        "container_id": row["container_id"], **dest}
        except Exception as exc:  # noqa: BLE001 — property 1
            logger.warning("custody move not recorded (%s → %s): %s", hid, destination, exc)
            return {"recorded": False, "reason": "unreachable", "detail": str(exc)[:300],
                    "uncertain": write_started}

    async def current_location(self, hid: str, *, user: str,
                               project: str | None = None,
                               refresh: bool = False) -> dict[str, Any]:
        """``{"found": bool, "hid", "container_id", "location_id", "location_name",
        "seat", "seat_conflict"}``; never raises (``found: None`` when the
        store could not answer).

        ``location_id`` / ``location_name`` are the **resolved** place — for a
        container seated in a rack on a deck slot, that slot — so every reader
        that asks "is the plate at L" (the run preflight, the manual-step
        source check) sees through seating. ``seat`` says what it sits in.

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
                place = resolved_location_id(row)
                name = None
                if place:
                    r = await client.get(f"{self.base_url}/locations/{place}",
                                         headers=self._headers(user, project))
                    if r.status_code == 200:
                        name = r.json().get("name")
                return {"found": True, "hid": hid, "container_id": row["container_id"],
                        "location_id": place, "location_name": name,
                        "raw_location_id": row.get("location_id"),
                        "seat": seat_of(row), "seat_conflict": bool(row.get("seat_conflict")),
                        "status": row.get("status")}
        except Exception as exc:  # noqa: BLE001
            return {"found": None, "hid": hid, "error": str(exc)[:200]}

    async def plates(self, *, user: str, projects: str = "", hid: str | None = None) -> list[dict]:
        """Top-level containers (no parent) joined with their **resolved** place
        name — a vial in a rack on a deck slot is at that slot — plus what they
        are seated in. Raises on transport failure — a read endpoint should
        say so, not render an empty lab."""
        rows, places = await self._containers_and_places(user=user, projects=projects, hid=hid)
        return [container_view(c, places) for c in rows if not c.get("parent_container_id")]

    async def _containers_and_places(self, *, user: str, projects: str = "",
                                     hid: str | None = None) -> tuple[list[dict], dict[str, dict]]:
        headers = {"X-Edge-Secret": self.secret, "X-Auth-User": user}
        if projects:
            headers["X-Auth-Projects"] = projects
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            params = {"hid": hid} if hid else {}
            rc = await client.get(f"{self.base_url}/containers", headers=headers, params=params)
            rc.raise_for_status()
            rl = await client.get(f"{self.base_url}/locations", headers=headers)
            rl.raise_for_status()
        return rc.json(), {str(loc["location_id"]): loc for loc in rl.json()}

    async def lab_map(self, *, user: str, projects: str = "", registry: Any, platforms: Any) -> dict:
        """The location-first lab map: every registry place grouped by platform
        section, with the containers resolved to it and their occupants nested
        (PLATE_TRACKING.md §11.4). Raises on transport failure."""
        rows, places = await self._containers_and_places(user=user, projects=projects)
        return build_lab_map(rows, places, registry=registry, platforms=platforms)

    async def history(self, container_id: str, *, user: str, projects: str = "") -> list[dict]:
        """A container's ledger rows, oldest first. Against a 0.16.0 record
        layer the moves of the carriers it was seated in are unioned in
        (``carried=true``), so "how did it get here" includes the rack's
        journey; an older record layer refuses unknown filters, so the flag
        is sent only when the live contract answers it."""
        headers = {"X-Edge-Secret": self.secret, "X-Auth-User": user}
        if projects:
            headers["X-Auth-Projects"] = projects
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            params: dict[str, str] = {"container_id": container_id}
            if await record_layer_contract(client, self.base_url) >= SEATING_CONTRACT:
                params["carried"] = "true"
            r = await client.get(f"{self.base_url}/container-actions", headers=headers, params=params)
            r.raise_for_status()
            return r.json()


def container_view(c: dict, places: dict[str, dict]) -> dict:
    """One dashboard row for a container: identity, status, the resolved place
    (name + equipment), the seat, the site manifest and the conflict flag."""
    place_id = resolved_location_id(c)
    loc = places.get(place_id or "")
    meta = c.get("meta") or {}
    sites = meta.get("sites") if isinstance(meta.get("sites"), list) else None
    return {
        "hid": c["hid"], "container_id": str(c["container_id"]),
        "container_type": c.get("container_type"), "model": c.get("model"),
        "status": c.get("status"),
        "location_id": place_id,
        "raw_location_id": str(c["location_id"]) if c.get("location_id") else None,
        "location": loc["name"] if loc else None,
        "equipment_id": loc.get("equipment_id") if loc else None,
        "project_id": c.get("project_id"),
        "seat": seat_of(c),
        "seat_conflict": bool(c.get("seat_conflict")),
        "sites": sites,
    }


#: Section the map files registry places under when `platforms.yaml` names no
#: platform for their equipment (benches, fridges, waste — or a device that is
#: not on any platform card).
OTHER_SECTION = {"id": "other", "title": "Benches, storage and waste"}


def build_lab_map(rows: list[dict], places: dict[str, dict], *, registry: Any, platforms: Any) -> dict:
    """Pure: ledger rows + ledger places + the two static configs → the map.

    - Every *active* registry place appears, with or without containers, under
      the platform section its equipment belongs to (``platforms.yaml``), else
      under :data:`OTHER_SECTION`. A ledger place the registry no longer lists
      is still shown (it holds containers) and marked ``registered: false``.
    - A container is filed at its **resolved** place. One seated on a carrier
      the caller may read is nested under that carrier (``occupants``); one
      seated on a carrier the caller may *not* read is filed at the place
      directly with ``chain_masked: true`` — it is there, inside something
      the caller cannot see.
    - Containers with no resolved place are ``unplaced``.
    Children (wells) are never listed: a plate is one node.
    """
    tops = [c for c in rows if not c.get("parent_container_id")]
    nodes: dict[str, dict] = {}
    for c in tops:
        view = container_view(c, places)
        nodes[view["container_id"]] = {**view, "occupants": [], "chain_masked": False}
    roots: list[dict] = []
    for node in nodes.values():
        seat = node["seat"]
        carrier = nodes.get(seat["container_id"]) if seat and seat.get("container_id") else None
        if carrier is not None:
            carrier["occupants"].append(node)
        else:
            if seat is not None:
                node["chain_masked"] = True
            roots.append(node)
    for node in nodes.values():
        node["occupants"].sort(key=lambda n: ((n["seat"] or {}).get("site") or "", n["hid"]))

    by_place: dict[str | None, list[dict]] = {}
    for node in roots:
        by_place.setdefault(node["location"], []).append(node)
    for bucket in by_place.values():
        bucket.sort(key=lambda n: n["hid"])

    section_of = platforms.equipment_to_section_id() if platforms is not None else {}
    titles = {s.id: s.title for s in platforms.sections} if platforms is not None else {}
    order = [s.id for s in platforms.sections] if platforms is not None else []
    sections: dict[str, dict] = {}

    def section_for(equipment: str | None) -> dict:
        sid = section_of.get(equipment or "", OTHER_SECTION["id"])
        if sid not in sections:
            sections[sid] = {"id": sid, "title": titles.get(sid, OTHER_SECTION["title"]), "places": []}
        return sections[sid]

    seen_names: set[str] = set()
    for entry in (registry.locations if registry is not None else []):
        if not entry.active:
            continue
        seen_names.add(entry.name)
        section_for(entry.equipment)["places"].append({
            "name": entry.name, "label": entry.label, "type": entry.type,
            "equipment_id": entry.equipment, "capacity": entry.capacity,
            "registered": True, "containers": by_place.pop(entry.name, []),
        })
    # Places the ledger knows (containers are there) but the registry no longer lists.
    for name, bucket in sorted(by_place.items(), key=lambda kv: kv[0] or ""):
        if name is None:
            continue
        ledger = next((loc for loc in places.values() if loc.get("name") == name), {})
        section_for(ledger.get("equipment_id"))["places"].append({
            "name": name, "label": ledger.get("label"), "type": ledger.get("location_type"),
            "equipment_id": ledger.get("equipment_id"), "capacity": ledger.get("capacity"),
            "registered": False, "containers": bucket,
        })
    ordered = [sections[sid] for sid in order if sid in sections]
    if OTHER_SECTION["id"] in sections:
        ordered.append(sections[OTHER_SECTION["id"]])
    return {
        "sections": ordered,
        "unplaced": by_place.get(None, []),
        "counts": {"containers": len(nodes), "placed": len(nodes) - len(by_place.get(None, []))},
    }


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


class SeatRequest(BaseModel):
    adapter_hid: str = Field(min_length=1, description="The carrier's hid — a rack, block or other adapter")
    site: str = Field(min_length=1, max_length=_SITE_MAX, description="Which of its sites, e.g. B3")


class MoveRequest(BaseModel):
    hid: str = Field(min_length=1, description="The container's barcode / Container.hid")
    to: str | None = Field(default=None, min_length=1,
                           description="Registry location name, e.g. bench/hte_staging (a place)")
    seat: SeatRequest | None = Field(
        default=None,
        description="Instead of a place: the carrier and site the container now sits in. "
                    "Its place becomes the carrier's.")
    note: str | None = Field(default=None, max_length=500)
    #: Who physically did it, when not the signed-in user (default: the user).
    performed_by: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _one_destination(self):
        if (self.to is None) == (self.seat is None):
            raise ValueError("name one destination: `to` (a place) or `seat` (a carrier and its site)")
        return self

    @property
    def destination(self) -> str:
        return self.to if self.to is not None else f"{self.seat.adapter_hid} @ {self.seat.site}"  # type: ignore[union-attr]


def _one_placement(at: str | None, seat: Any) -> None:
    if at is not None and seat is not None:
        raise ValueError("a container is received at a place (`at`) OR in a seat (`seat`), not both")


def _addresses(values: list[str] | None, what: str) -> list[str] | None:
    if values is None:
        return None
    names = [str(v).strip() for v in values]
    bad = [n for n in names if not valid_site(n)]
    if bad:
        raise ValueError(f"invalid {what}: {bad!r} (short, no spaces, no slash)")
    if len(set(names)) != len(names):
        raise ValueError(f"{what} carry duplicate names")
    return names


class RegisterRequest(BaseModel):
    """Register one new container, optionally at a place or in a seat.

    What it *is* comes from, in order of precedence: the explicit fields
    (``positions`` for a plate's wells, ``sites`` for an adapter's manifest),
    the ``wells`` shorthand (a standard grid), or the labware definition this
    stack holds for ``model`` (repo, builder upload or Opentrons standard
    set), which also supplies ``container_type`` when it is omitted.
    """

    hid: str = Field(min_length=1, max_length=256, description="The barcode / label; unique for all time")
    container_type: str | None = Field(
        default=None, description=f"One of {sorted(CONTAINER_TYPES)}; omitted = from the model's definition")
    model: str | None = Field(default=None, max_length=256, description="Labware load name, e.g. corning_96_wellplate_360ul_flat")
    wells: int | None = Field(default=None, description=f"Standard grid shorthand: one of {sorted(WELL_LAYOUTS)}")
    positions: list[str] | None = Field(default=None, description="Explicit well list (plates); overrides wells/model")
    sites: list[str] | None = Field(default=None, description="Explicit site manifest (racks, blocks, any seat-offering container)")
    at: str | None = Field(default=None, min_length=1, description="Registry place name to receive it at")
    seat: SeatRequest | None = Field(default=None, description="Carrier and site to receive it in")
    status: Literal["empty", "in_use", "dirty"] = "empty"
    project: str | None = Field(default=None, min_length=1,
                                description="Owning project title; omitted = lab-scoped (the normal case for labware)")
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _shape(self):
        if self.container_type is not None and self.container_type not in CONTAINER_TYPES:
            raise ValueError(f"container_type must be one of {sorted(CONTAINER_TYPES)} (wells are minted, not registered)")
        if self.wells is not None and self.wells not in WELL_LAYOUTS:
            raise ValueError(f"wells must be one of {sorted(WELL_LAYOUTS)}")
        self.positions = _addresses(self.positions, "positions")
        self.sites = _addresses(self.sites, "sites")
        if self.container_type in ADAPTER_TYPES and (self.positions or self.wells):
            raise ValueError(f"a {self.container_type} is an adapter: it has sites (a manifest), not wells")
        _one_placement(self.at, self.seat)
        return self

    @property
    def destination(self) -> str | None:
        if self.at is not None:
            return self.at
        return f"{self.seat.adapter_hid} @ {self.seat.site}" if self.seat else None


def _refusal(result: dict[str, Any], *, hid: str, place: str | None) -> HTTPException:
    """The HTTP status a recorder refusal deserves: the ledger's own 409/422
    pass through; a local pre-check names what was wrong; anything else is a 502."""
    reason = result.get("reason")
    if reason == "unknown_container":
        return HTTPException(status_code=404, detail=f"no container with hid {hid!r} is registered")
    if reason == "unknown_adapter":
        return HTTPException(status_code=404, detail=f"no container with hid {result.get('adapter_hid')!r} is registered to seat into")
    if reason == "hid_taken":
        return HTTPException(status_code=409, detail=f"hid {hid!r} is already registered — hids are never reused")
    if reason == "unknown_location":
        return HTTPException(status_code=422, detail=f"{place!r} is not seeded in the record layer — run scripts/seed_locations.py")
    if reason == "unknown_site":
        return HTTPException(status_code=422, detail=f"{result.get('adapter_hid')} has no site {result.get('site')!r}; its sites are {result.get('sites')}")
    if reason in ("invalid_site", "self_seat"):
        return HTTPException(status_code=422, detail=f"not a valid seat: {result}")
    if reason == "seating_unsupported":
        return HTTPException(status_code=503, detail="the record layer does not record seats yet "
                             f"(contract {result.get('contract') or 'unknown'} < 0.16.0)")
    if reason == "http_409":
        return HTTPException(status_code=409, detail=f"the ledger refused: {result.get('detail')}")
    if reason == "http_422":
        return HTTPException(status_code=422, detail=f"the ledger rejected the row: {result.get('detail')}")
    return HTTPException(status_code=502, detail=f"record layer refused the write: {result}")


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
        """Record a bench-top move: container ``hid`` is now at place ``to``,
        or in ``seat`` (a carrier's site — its place is then the carrier's).

        The same ledger row the executor writes for a robot move, with the
        human as ``performed_by``. A place is checked against the registry
        first (a typo must not reach the ledger), then resolved in the record
        layer; a seat is checked against the carrier's registered site
        manifest. Occupancy is recorded and flagged, never refused (D2). A
        ledger refusal keeps its status: 409 when the container is not where
        the ledger last saw it, 422 when the ledger rejects the row itself.
        """
        user = _signed_in(request)
        recorder = custody_recorder()
        if recorder is None:
            raise HTTPException(status_code=503, detail="record layer not configured — custody cannot be recorded")
        cfg = getattr(request.app.state, "locations_config", None)
        if body.to is not None and cfg is not None:
            entry = cfg.by_name(body.to)
            if entry is None or not entry.active:
                raise HTTPException(status_code=422, detail=f"{body.to!r} is not an active place in locations.yaml")
        seat = Seat(body.seat.adapter_hid, body.seat.site) if body.seat else None
        seat_dict = body.seat.model_dump() if body.seat else None
        projects = request.headers.get("x-auth-projects", "")
        result = await recorder.record_move(
            hid=body.hid, to=body.to, seat=seat, performed_by=body.performed_by or user,
            recorder=user, project=projects.split(",")[0].strip() or None,
            params={"reason": "bench", "note": body.note, "via": "dashboard"},
        )
        outcome = "ok" if result.get("recorded") else result.get("reason", "failed")
        await record_custody_event(
            request, "control_action", device_id=CUSTODY_DEVICE_ID,
            message=f"{user} move {body.hid} → {body.destination} → {outcome}",
            payload={"action": "custody.move", "method": "POST", "owner": user,
                     "outcome": outcome, "detail": {"hid": body.hid, "to": body.to, "seat": seat_dict}},
        )
        if result.get("recorded"):
            await record_custody_event(
                request, PLATE_MOVED, device_id=CUSTODY_DEVICE_ID,
                message=f"{body.hid} → {body.destination} (by {body.performed_by or user})",
                payload={"hid": body.hid, "to": body.to, "seat": seat_dict,
                         "performed_by": body.performed_by or user,
                         "recorded_by": user, "source": "bench",
                         "action_id": result.get("action_id")},
            )
            return {"recorded": True, "hid": body.hid, "to": body.destination, "seat": seat_dict, **result}
        if result.get("reason") == "http_409":
            raise HTTPException(status_code=409, detail=f"the ledger says {body.hid!r} is not where it was last seen — re-read the map and decide again: {result.get('detail')}")
        raise _refusal(result, hid=body.hid, place=body.to)

    @router.post("/register")
    async def register(body: RegisterRequest, request: Request) -> dict:
        """Register a new container — the dashboard's front door for the bench
        (bitácora's is ``propose_plate_registration``). A plate is minted with
        its wells, an adapter with its site manifest, both from the labware
        definition this stack holds for ``model`` unless given explicitly; it
        may be received at a place or in a seat in the same ledger
        transaction. Lab-scoped unless ``project`` is named. A taken hid is a
        409 — hids are never reused.
        """
        from .labware import find_definition

        user = _signed_in(request)
        recorder = custody_recorder()
        if recorder is None:
            raise HTTPException(status_code=503, detail="record layer not configured — custody cannot be recorded")
        cfg = getattr(request.app.state, "locations_config", None)
        if body.at is not None and cfg is not None:
            entry = cfg.by_name(body.at)
            if entry is None or not entry.active:
                raise HTTPException(status_code=422, detail=f"{body.at!r} is not an active place in locations.yaml")
        found = find_definition(body.model) if body.model else None
        layout = labware_layout(found["definition"], source=found["source"]) if found else None
        container_type = body.container_type or (layout["container_type"] if layout else None)
        if container_type is None:
            raise HTTPException(status_code=422, detail=(
                f"say what {body.hid!r} is: container_type, or a model the labware store knows"
                + (f" ({body.model!r} is not one)" if body.model else "")))
        if container_type in ADAPTER_TYPES and (body.positions or body.wells):
            raise HTTPException(status_code=422, detail=f"a {container_type} is an adapter: it has sites, not wells")
        meta: dict[str, Any] = {"registered_via": "dashboard"}
        if body.note:
            meta["note"] = body.note
        if layout:
            meta["definition"] = layout["definition"]
        positions: list[str] | None = None
        sites: list[str] | None = body.sites
        if container_type in WELL_BEARING_TYPES:
            positions = body.positions or (grid_positions(body.wells) if body.wells else None) \
                or (layout["addresses"] if layout and layout["addresses"] else None)
            if not positions:
                raise HTTPException(status_code=422, detail=(
                    f"a {container_type} is registered with its wells: give positions, "
                    f"wells ({sorted(WELL_LAYOUTS)}) or a model the labware store knows"))
        elif container_type in ADAPTER_TYPES and sites is None and layout and layout["addresses"]:
            sites = layout["addresses"]
            meta["sites_definition"] = layout["definition"]
        seat = Seat(body.seat.adapter_hid, body.seat.site) if body.seat else None
        projects = request.headers.get("x-auth-projects", "")
        result = await recorder.register_container(
            hid=body.hid, container_type=container_type, recorder=user,
            project=projects.split(",")[0].strip() or None, owner_project=body.project,
            model=body.model, status=body.status, positions=positions, sites=sites, meta=meta,
            at=body.at, seat=seat,
        )
        outcome = "ok" if result.get("registered") else result.get("reason", "failed")
        await record_custody_event(
            request, "control_action", device_id=CUSTODY_DEVICE_ID,
            message=f"{user} register {body.hid} ({container_type}) → {body.destination or 'unplaced'} → {outcome}",
            payload={"action": "custody.register", "method": "POST", "owner": user, "outcome": outcome,
                     "detail": {"hid": body.hid, "container_type": container_type, "model": body.model,
                                "positions": len(positions or []), "sites": sites, "at": body.at,
                                "seat": body.seat.model_dump() if body.seat else None}},
        )
        if result.get("registered"):
            return {**result, "model": body.model, "at": body.at,
                    "seat": body.seat.model_dump() if body.seat else None,
                    "destination": body.destination}
        raise _refusal(result, hid=body.hid, place=body.at)

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

    @router.get("/map")
    async def lab_map(request: Request) -> dict:
        """The location-first lab map: registry places grouped by platform,
        each with the containers resolved to it (seated ones nested under
        their carrier). A read-through; the dashboard keeps no copy."""
        recorder = custody_recorder()
        if recorder is None:
            raise HTTPException(status_code=503, detail="record layer not configured")
        user = _signed_in(request)
        try:
            return await recorder.lab_map(
                user=user, projects=request.headers.get("x-auth-projects", ""),
                registry=getattr(request.app.state, "locations_config", None),
                platforms=getattr(request.app.state, "platforms_config", None),
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"record layer unreachable: {exc}") from exc

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
