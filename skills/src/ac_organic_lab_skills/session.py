"""Lab session: workflow-facing entry point.

A ``LabSession`` bundles the loaded ``Registry`` with a shared
``httpx.AsyncClient`` and (later) the role binding, claims manager, and skill
catalog. It is the object returned by ``Lab.connect(...)`` and used as an
async context manager.

v0.1 surface:

* ``session.get(equipment_id) -> EquipmentClient``
    raises :class:`EquipmentInMaintenance` when the entry is disabled or has a
    non-null ``maintenance`` block; raises :class:`RegistryError` for unknown
    ids.

Reserved for v0.2 and later (placeholders only here):

* ``session.role(name) -> EquipmentClient``  (binding-driven)
* ``session.skills() -> list[Skill]``
* ``session.add_interlock(fn)`` / ``session.validate_plan(plan)``
"""

from __future__ import annotations

from types import TracebackType
from typing import Mapping

import httpx

from .client import EquipmentClient
from .exceptions import EquipmentInMaintenance, RegistryError
from .registry import Registry


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
        return EquipmentClient(entry, self._http)

    def role(self, role_name: str) -> EquipmentClient:
        """Resolve a role binding to an ``EquipmentClient``.

        v0.1 supports a binding dict (``{"sealer": "plateloc", ...}``) passed
        on ``Lab.connect``. The richer ``session.skills()`` catalog and typed
        per-kind wrappers (``role.seal_start(...)``) land in v0.2.
        """

        if role_name not in self._binding:
            raise RegistryError(
                f"Role {role_name!r} is not bound; pass binding={{...}} to "
                f"Lab.connect or set {role_name!r} explicitly."
            )
        return self.get(self._binding[role_name])


__all__ = ["LabSession"]
