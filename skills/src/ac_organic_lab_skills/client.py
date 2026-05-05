"""Per-equipment client object.

``EquipmentClient`` wraps one ``EquipmentEntry`` together with the session's
shared ``httpx.AsyncClient`` and exposes the read-side device contract from
``docs/STATUS_SPEC.md``: ``status()``, ``probe()``, ``health()``.

Control wrappers (``command()`` plus typed per-kind methods like
``seal_start``) land in v0.2 with the catalog. The v0.1 client is read-only.
"""

from __future__ import annotations

import httpx
from pydantic import ValidationError

from .exceptions import EquipmentUnreachable
from .models import EquipmentStatus, HealthResponse, ProbeResponse
from .registry import EquipmentEntry


class EquipmentClient:
    """Workflow-facing handle for one device.

    Constructed from a ``LabSession``; do not instantiate directly. The session
    owns the shared ``httpx.AsyncClient`` and the registry entry; this class is
    a thin convenience layer over them.
    """

    def __init__(self, entry: EquipmentEntry, http: httpx.AsyncClient) -> None:
        self._entry = entry
        self._http = http

    @property
    def entry(self) -> EquipmentEntry:
        return self._entry

    @property
    def equipment_id(self) -> str:
        return self._entry.id

    @property
    def base_url(self) -> str:
        if not self._entry.base_url:
            raise EquipmentUnreachable(
                self._entry.id,
                "no base_url configured in equipment.yaml",
            )
        return self._entry.base_url.rstrip("/")

    async def status(self) -> EquipmentStatus:
        """Return the device's live ``GET /status`` envelope.

        Always re-reads from the device (no caching). Workflow code about to
        issue a control command must re-read ``status()`` directly because
        cache staleness measured in seconds is forever in robotics.
        """

        return await self._fetch_envelope(
            self._entry.status_path, EquipmentStatus, label="status"
        )

    async def probe(self) -> ProbeResponse:
        """Return the device's ``GET /`` identity probe."""

        return await self._fetch_envelope("/", ProbeResponse, label="probe")

    async def health(self) -> HealthResponse:
        """Return the device's ``GET /health`` liveness response."""

        return await self._fetch_envelope("/health", HealthResponse, label="health")

    async def _fetch_envelope(
        self,
        path: str,
        model: type,
        *,
        label: str,
    ):
        url = self.base_url + (path if path.startswith("/") else f"/{path}")
        try:
            response = await self._http.get(
                url, timeout=self._entry.poll_timeout_seconds
            )
        except httpx.TimeoutException as exc:
            raise EquipmentUnreachable(
                self._entry.id, f"timeout calling {url}: {exc}"
            ) from exc
        except httpx.ConnectError as exc:
            raise EquipmentUnreachable(
                self._entry.id, f"cannot connect to {url}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise EquipmentUnreachable(
                self._entry.id, f"HTTP error calling {url}: {exc}"
            ) from exc

        if response.status_code >= 500:
            raise EquipmentUnreachable(
                self._entry.id,
                f"{url} returned HTTP {response.status_code}",
            )
        if response.status_code >= 400:
            raise EquipmentUnreachable(
                self._entry.id,
                f"{url} returned HTTP {response.status_code}: "
                f"{response.text[:200]}",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise EquipmentUnreachable(
                self._entry.id, f"{url} did not return JSON: {exc}"
            ) from exc

        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise EquipmentUnreachable(
                self._entry.id,
                f"{url} {label} body does not match the spec: {exc}",
            ) from exc


__all__ = ["EquipmentClient"]
