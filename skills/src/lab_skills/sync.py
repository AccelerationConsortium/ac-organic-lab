"""Sync facade over the async SDK for notebooks and one-shot scripts.

The async SDK (``Lab.connect()`` / ``LabSession`` / ``EquipmentClient``) is
the source of truth and is what workflow runners and the MCP server use.
For ad-hoc work in a Jupyter notebook or a plain Python REPL, awaiting
every call adds friction and re-entrancy traps. This module wraps the async
classes in thin sync proxies that share a private event loop owned by the
session.

Example::

    from lab_skills.sync import Lab

    with Lab.connect(binding={"sealer": "plateloc"}) as lab:
        sealer = lab.role("sealer")
        envelope = sealer.status()
        sealer.command(
            "/control/seal/start",
            {"temperature_c": 170, "seconds": 3.0},
        )

The proxies do not re-implement any control logic; they delegate to the
async classes via ``loop.run_until_complete``. Same exceptions, same return
values, same precedence rules - just no ``await``.

Notes
-----
* Each ``Lab.connect(...)`` call owns its own event loop. The loop is
  created on ``__enter__`` and closed on ``__exit__``. Do not share a sync
  ``LabSession`` across threads.
* Inside an already-running event loop (e.g. when a Jupyter notebook is
  already async), prefer the async API directly to avoid nested-loop errors.
"""

from __future__ import annotations

import asyncio
import os
from types import TracebackType
from typing import Any, Mapping

from pydantic import BaseModel

from .skill_catalog import Skill
from .client import EquipmentClient as _AsyncEquipmentClient
from .lab import Lab as _AsyncLab
from .models import EquipmentStatus, HealthResponse, ProbeResponse
from .plan import Plan, PlanReport, PlanRunReport, execute_plan, validate_plan
from .registry import EquipmentEntry, Registry
from .session import LabSession as _AsyncLabSession


class _SyncEquipmentClient:
    """Sync proxy around an async :class:`EquipmentClient`.

    Constructed by :class:`SyncLabSession`; do not instantiate directly.
    """

    def __init__(
        self,
        async_client: _AsyncEquipmentClient,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._async_client = async_client
        self._loop = loop

    @property
    def entry(self) -> EquipmentEntry:
        return self._async_client.entry

    @property
    def equipment_id(self) -> str:
        return self._async_client.equipment_id

    @property
    def base_url(self) -> str:
        return self._async_client.base_url

    def status(self) -> EquipmentStatus:
        return self._loop.run_until_complete(self._async_client.status())

    def probe(self) -> ProbeResponse:
        return self._loop.run_until_complete(self._async_client.probe())

    def health(self) -> HealthResponse:
        return self._loop.run_until_complete(self._async_client.health())

    def command(
        self,
        path: str,
        body: BaseModel | Mapping[str, Any] | None = None,
        *,
        response_schema: type[BaseModel] | None = None,
    ) -> Any:
        return self._loop.run_until_complete(
            self._async_client.command(path, body, response_schema=response_schema)
        )


class SyncLabSession:
    """Sync proxy around an async :class:`LabSession`.

    Use as a regular context manager (``with Lab.connect(...) as lab:``).
    Each instance owns its own event loop; the loop is created on
    ``__enter__`` and closed on ``__exit__``.
    """

    def __init__(self, async_session: _AsyncLabSession) -> None:
        self._async_session = async_session
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def registry(self) -> Registry:
        return self._async_session.registry

    @property
    def binding(self) -> Mapping[str, str]:
        return self._async_session.binding

    def __enter__(self) -> "SyncLabSession":
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._async_session.__aenter__())
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        loop = self._loop
        assert loop is not None
        try:
            loop.run_until_complete(
                self._async_session.__aexit__(exc_type, exc, tb)
            )
        finally:
            loop.close()
            self._loop = None

    def get(self, equipment_id: str) -> _SyncEquipmentClient:
        return self._wrap(self._async_session.get(equipment_id))

    def role(self, role_name: str) -> _SyncEquipmentClient:
        return self._wrap(self._async_session.role(role_name))

    def skills(self) -> list[Skill]:
        loop = self._loop
        if loop is None:
            raise RuntimeError(
                "SyncLabSession is not active; use `with Lab.connect(...) as lab:`"
            )
        return loop.run_until_complete(self._async_session.skills())

    def validate_plan(self, plan: Plan) -> PlanReport:
        """Offline plan validation (no HTTP). Sync mirror of
        :func:`lab_skills.validate_plan`; needs no running loop."""

        return validate_plan(plan, self._async_session)

    def execute_plan(
        self,
        plan: Plan,
        *,
        owner: str,
        ttl_s: float = 30.0,
        dry_run: bool = False,
    ) -> PlanRunReport:
        """Execute a plan against live hardware. Sync mirror of
        :func:`lab_skills.execute_plan`; the whole run happens inside one
        ``run_until_complete`` so per-step claim heartbeats fire normally."""

        loop = self._loop
        if loop is None:
            raise RuntimeError(
                "SyncLabSession is not active; use `with Lab.connect(...) as lab:`"
            )
        return loop.run_until_complete(
            execute_plan(
                plan, self._async_session, owner=owner, ttl_s=ttl_s, dry_run=dry_run
            )
        )

    def _wrap(self, async_client: _AsyncEquipmentClient) -> _SyncEquipmentClient:
        if self._loop is None:
            raise RuntimeError(
                "SyncLabSession is not active; use `with Lab.connect(...) as lab:`"
            )
        return _SyncEquipmentClient(async_client, self._loop)


class Lab:
    """Static factory for :class:`SyncLabSession` instances.

    Mirrors the async :class:`lab_skills.Lab` surface, returning a
    sync session that can be used with a plain ``with`` statement instead of
    ``async with``.
    """

    @staticmethod
    def connect(
        *,
        registry: Registry | None = None,
        registry_path: str | os.PathLike | None = None,
        binding: Mapping[str, str] | None = None,
        http_timeout: float = 5.0,
    ) -> SyncLabSession:
        async_session = _AsyncLab.connect(
            registry=registry,
            registry_path=registry_path,
            binding=binding,
            http_timeout=http_timeout,
        )
        return SyncLabSession(async_session)


__all__ = ["Lab", "SyncLabSession"]
