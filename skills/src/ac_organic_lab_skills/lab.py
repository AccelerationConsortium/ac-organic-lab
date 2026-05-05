"""``Lab.connect()`` - the workflow's entry point into the SDK.

Usage::

    from ac_organic_lab_skills import Lab, wait_until_state

    async with Lab.connect(binding={"sealer": "plateloc"}) as lab:
        sealer = lab.role("sealer")
        await wait_until_state(sealer, "ready", timeout=10)
        envelope = await sealer.status()

The class is currently a thin factory; v0.5 will add a ``service_url=...``
mode that talks to a running ``ac-organic-lab-skills serve`` HTTP service
instead of loading the registry locally.
"""

from __future__ import annotations

import os
from typing import Mapping

from .registry import Registry, load_registry
from .session import LabSession


class Lab:
    """Static factory for ``LabSession`` instances."""

    @staticmethod
    def connect(
        *,
        registry: Registry | None = None,
        registry_path: str | os.PathLike | None = None,
        binding: Mapping[str, str] | None = None,
        http_timeout: float = 5.0,
    ) -> LabSession:
        """Build a ``LabSession`` and return it (use as ``async with``).

        Either pass an already-loaded ``registry`` or a ``registry_path`` that
        :func:`load_registry` understands. The default (neither) walks parent
        directories looking for ``equipment.yaml`` and works inside the
        monorepo without configuration.
        """

        if registry is None:
            registry = load_registry(registry_path)
        return LabSession(
            registry,
            binding=binding,
            http_timeout=http_timeout,
        )


__all__ = ["Lab"]
