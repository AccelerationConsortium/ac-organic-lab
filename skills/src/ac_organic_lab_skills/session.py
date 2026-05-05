"""Lab session: workflow-facing entry point.

A ``LabSession`` bundles the loaded ``Registry`` with a shared
``httpx.AsyncClient`` and the role binding. It is the object returned by
``Lab.connect(...)`` and used as an async context manager.

Surface as of v0.2:

* ``session.get(equipment_id) -> EquipmentClient``
* ``session.role(name) -> EquipmentClient``  (binding-driven; v0.3 returns a
  per-kind subclass with typed methods)
* ``await session.skills() -> list[Skill]`` (catalog x live status)

Reserved for v0.3 and later:

* ``session.add_interlock(fn)`` / ``session.validate_plan(plan)``
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Mapping

import httpx

from .catalog import Skill, SkillDef, skills_for
from .client import EquipmentClient
from .exceptions import (
    EquipmentInMaintenance,
    EquipmentUnreachable,
    RegistryError,
)
from .kinds import client_for
from .models import EquipmentStatus
from .registry import EquipmentEntry, Registry


class LabSession:
    """Async context manager owning the shared HTTP client and registry."""

    def __init__(
        self,
        registry: Registry,
        *,
        binding: Mapping[str, str] | None = None,
        http_timeout: float = 5.0,
    ) -> None:
        self._registry = registry
        self._binding: dict[str, str] = dict(binding or {})
        self._http_timeout = http_timeout
        self._http: httpx.AsyncClient | None = None

    @property
    def registry(self) -> Registry:
        return self._registry

    @property
    def binding(self) -> Mapping[str, str]:
        return dict(self._binding)

    async def __aenter__(self) -> "LabSession":
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(self._http_timeout))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def get(self, equipment_id: str) -> EquipmentClient:
        """Resolve an equipment id to an ``EquipmentClient``.

        Raises:
            RegistryError: the id is not present in ``equipment.yaml``.
            EquipmentInMaintenance: the entry is ``enabled: false`` or has a
                non-null ``maintenance`` block.
        """

        entry = self._registry.by_id(equipment_id)
        if entry is None:
            raise RegistryError(f"Unknown equipment id: {equipment_id}")
        if not entry.enabled or entry.maintenance is not None:
            m = entry.maintenance
            raise EquipmentInMaintenance(
                equipment_id=equipment_id,
                reason=m.reason if m is not None else None,
                until=m.until if m is not None else None,
                contact=m.contact if m is not None else None,
            )
        if self._http is None:
            raise RuntimeError(
                "LabSession is not active; use `async with Lab.connect(...) as lab:`"
            )
        return client_for(entry, self._http)

    def role(self, role_name: str) -> EquipmentClient:
        """Resolve a role binding to an ``EquipmentClient``.

        v0.1 supports a binding dict (``{"sealer": "plateloc", ...}``) passed
        on ``Lab.connect``; v0.2 wires up :meth:`skills` on top of it; v0.3
        upgrades the return type to a per-kind subclass with typed control
        methods (``role.seal_start(...)``).
        """

        if role_name not in self._binding:
            raise RegistryError(
                f"Role {role_name!r} is not bound; pass binding={{...}} to "
                f"Lab.connect or set {role_name!r} explicitly."
            )
        return self.get(self._binding[role_name])

    async def skills(self) -> list[Skill]:
        """Return the runtime skill catalog for every bound role.

        For each ``(role, equipment_id)`` in the binding:

        1. Look up :class:`SkillDef`s for the entry's ``kind`` from
           :data:`SKILL_REGISTRY` (empty list when the kind has no registered
           capabilities, e.g. ``robot_arm`` in v0.2).
        2. Fetch the device's live ``/status`` (concurrently across roles).
        3. Compute :class:`Skill.available` per role x def using:

           * ``status.allowed_actions`` (STATUS_SPEC v1.1+) when present:
             ``available iff def.name in allowed_actions``.
           * Else fall back to ``status.equipment_status in def.requires_states``
             (or the def's ``requires_states`` is empty - treated as
             "no constraint").

        Roles bound to disabled / maintenance / unreachable equipment still
        appear in the catalog with ``available=False`` and a human-readable
        ``reason``; this lets the dashboard and agents see "what would be
        possible if this device came back online".
        """

        if self._http is None:
            raise RuntimeError(
                "LabSession is not active; use `async with Lab.connect(...) as lab:`"
            )

        # Resolve each role to (entry, defs, maintenance_reason). When the
        # role is bound to an unknown / disabled / maintenance entry, defer
        # the failure to the per-skill availability calculation rather than
        # raising - skills() is supposed to give a complete picture.
        per_role: list[
            tuple[str, EquipmentEntry | None, list[SkillDef], str | None]
        ] = []
        for role_name, equipment_id in self._binding.items():
            entry = self._registry.by_id(equipment_id)
            if entry is None:
                per_role.append(
                    (role_name, None, [], f"unknown equipment id: {equipment_id}")
                )
                continue
            defs = skills_for(entry.kind)
            if not entry.enabled or entry.maintenance is not None:
                m = entry.maintenance
                if m is not None:
                    reason = f"under maintenance: {m.reason}"
                else:
                    reason = "device disabled (enabled: false)"
                per_role.append((role_name, entry, defs, reason))
                continue
            per_role.append((role_name, entry, defs, None))

        # Fan out one /status fetch per *reachable* role.
        async def _fetch(entry: EquipmentEntry) -> EquipmentStatus | EquipmentUnreachable:
            assert self._http is not None
            client = EquipmentClient(entry, self._http)
            try:
                return await client.status()
            except EquipmentUnreachable as exc:
                return exc

        fetch_tasks: dict[str, asyncio.Task] = {}
        for role_name, entry, defs, maintenance_reason in per_role:
            if entry is None or maintenance_reason is not None or not defs:
                # No fetch needed: either the role has no SkillDefs to
                # evaluate, or its availability is determined by maintenance
                # state alone.
                continue
            fetch_tasks[role_name] = asyncio.create_task(_fetch(entry))
        if fetch_tasks:
            await asyncio.gather(*fetch_tasks.values())

        # Build the Skill list.
        skills: list[Skill] = []
        for role_name, entry, defs, maintenance_reason in per_role:
            if entry is None:
                continue

            status: EquipmentStatus | None = None
            unreachable_reason: str | None = None
            if role_name in fetch_tasks:
                fetched = fetch_tasks[role_name].result()
                if isinstance(fetched, EquipmentUnreachable):
                    unreachable_reason = f"unreachable: {fetched.message}"
                else:
                    status = fetched

            for d in defs:
                available, reason = _availability(
                    d, status, maintenance_reason, unreachable_reason
                )
                skills.append(
                    Skill(
                        name=d.name,
                        role=role_name,
                        equipment_id=entry.id,
                        kind=entry.kind,
                        description=d.description,
                        args_schema=d.args_schema,
                        estimated_duration_s=d.estimated_duration_s,
                        available=available,
                        reason=reason,
                    )
                )
        return skills


def _availability(
    skill_def: SkillDef,
    status: EquipmentStatus | None,
    maintenance_reason: str | None,
    unreachable_reason: str | None,
) -> tuple[bool, str | None]:
    """Compute (available, reason) for a single SkillDef given live state.

    Precedence (per ``docs/SKILLS_CATALOG.md`` "Three sources of truth"):

    1. Maintenance / disabled wins everything.
    2. Unreachable device -> not available.
    3. ``status.allowed_actions`` (v1.1+ device, non-empty) is authoritative.
    4. ``status.equipment_status in def.requires_states`` is the v1.0
       fallback. Empty ``requires_states`` is treated as "no constraint".
    """

    if maintenance_reason is not None:
        return False, maintenance_reason
    if unreachable_reason is not None:
        return False, unreachable_reason
    if status is None:
        return False, "no status available"

    # STATUS_SPEC v1.1 forward-compat: device-declared allowed_actions wins
    # whenever the device reports a non-empty list.
    if status.allowed_actions:
        if skill_def.name in status.allowed_actions:
            return True, None
        return False, (
            f"device does not currently allow {skill_def.name!r} "
            f"(equipment_status={status.equipment_status!r})"
        )

    # STATUS_SPEC v1.0 fallback.
    if not skill_def.requires_states:
        return True, None
    if status.equipment_status in skill_def.requires_states:
        return True, None
    return False, (
        f"equipment_status={status.equipment_status!r}; "
        f"requires one of {sorted(skill_def.requires_states)!r}"
    )


__all__ = ["LabSession"]
