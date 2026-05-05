"""Per-equipment client object.

``EquipmentClient`` wraps one ``EquipmentEntry`` together with the session's
shared ``httpx.AsyncClient`` and exposes both the read-side device contract
from ``docs/STATUS_SPEC.md`` (``status()``, ``probe()``, ``health()``) and the
generic control-side surface (``command()``).

Hand-written typed wrappers per device kind (``PlateSealerClient.seal_start``,
etc.) land in v0.3 and call ``command()`` under the hood. v0.2 ships the
generic ``command()`` so workflow code can already invoke any device's
``/control/*`` endpoint with typed exceptions; the typed methods are
ergonomic sugar over this primitive.
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx
from pydantic import BaseModel, ValidationError

from .exceptions import (
    BadRequest,
    EquipmentBusy,
    EquipmentUnreachable,
    RequiresInit,
)
from .models import EquipmentStatus, HealthResponse, ProbeResponse
from .registry import EquipmentEntry


# Substrings inside an HTTP 4xx detail body that identify the
# "device hardware not initialized / not connected" condition. Mapped to
# :class:`RequiresInit` rather than :class:`BadRequest` so workflow code can
# react with a typed retry-after-startup. Match is case-insensitive.
_REQUIRES_INIT_HINTS: tuple[str, ...] = (
    "not connected",
    "not initialized",
    "requires_init",
    "requires init",
    "awaiting startup",
    "post /control/startup",
)


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

    # -- Read-side: STATUS_SPEC v1.0 endpoints -------------------------------

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

    # -- Control-side: generic typed POST ------------------------------------

    async def command(
        self,
        path: str,
        body: BaseModel | Mapping[str, Any] | None = None,
        *,
        response_schema: type[BaseModel] | None = None,
    ) -> Any:
        """POST ``body`` to ``path`` on this device and return the response.

        ``body`` may be a Pydantic model (preferred; serialised via
        ``model_dump()``), a plain mapping, or ``None``. If
        ``response_schema`` is provided the response JSON is validated against
        it and the resulting model is returned; otherwise the raw decoded JSON
        is returned (or ``None`` for empty bodies).

        HTTP error mapping
        ------------------
        * ``409`` -> :class:`EquipmentBusy`
        * ``400`` / ``422`` whose detail mentions "not connected" /
          "not initialized" / similar -> :class:`RequiresInit`
        * other ``4xx`` -> :class:`BadRequest`
        * ``5xx``, transport timeout / connect error, or unparseable JSON ->
          :class:`EquipmentUnreachable`

        The typed per-kind subclasses in v0.3 (``PlateSealerClient`` etc.)
        wrap this with concrete arg schemas; workflow code that wants to call
        an endpoint without a wrapper can use ``command()`` directly.
        """

        url = self._url(path)
        payload = self._prepare_body(body)

        try:
            response = await self._http.post(
                url,
                json=payload,
                timeout=self._entry.poll_timeout_seconds,
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
            self._raise_for_4xx(response, url)

        return self._decode_response(response, response_schema, url=url)

    # -- Internals -----------------------------------------------------------

    def _url(self, path: str) -> str:
        return self.base_url + (path if path.startswith("/") else f"/{path}")

    @staticmethod
    def _prepare_body(
        body: BaseModel | Mapping[str, Any] | None,
    ) -> Any:
        if body is None:
            return None
        if isinstance(body, BaseModel):
            return body.model_dump(mode="json", exclude_none=False)
        return dict(body)

    def _raise_for_4xx(self, response: httpx.Response, url: str) -> None:
        detail = self._extract_detail(response)
        status_code = response.status_code

        if status_code == 409:
            raise EquipmentBusy(
                self._entry.id,
                detail or f"{url} returned HTTP 409",
                http_status=status_code,
            )

        if status_code in (400, 422) and self._looks_like_requires_init(detail):
            raise RequiresInit(
                self._entry.id,
                detail or f"{url} returned HTTP {status_code}",
                http_status=status_code,
            )

        raise BadRequest(
            self._entry.id,
            detail or f"{url} returned HTTP {status_code}",
            http_status=status_code,
        )

    @staticmethod
    def _extract_detail(response: httpx.Response) -> str:
        """Pull a human-readable detail string out of a 4xx response.

        FastAPI uses ``{"detail": "..."}`` (string) for ``HTTPException`` and
        ``{"detail": [...]}`` (list of validation errors) for Pydantic 422s.
        Other servers may return a plain string body. Falls back to the raw
        response text (truncated) so the operator always has something
        actionable.
        """

        try:
            payload = response.json()
        except ValueError:
            return response.text[:200]

        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("error") or payload.get("message")
            if isinstance(detail, str):
                return detail
            if detail is not None:
                return str(detail)
        if isinstance(payload, str):
            return payload
        return str(payload)[:200]

    @staticmethod
    def _looks_like_requires_init(detail: str) -> bool:
        if not detail:
            return False
        lowered = detail.lower()
        return any(hint in lowered for hint in _REQUIRES_INIT_HINTS)

    def _decode_response(
        self,
        response: httpx.Response,
        response_schema: type[BaseModel] | None,
        *,
        url: str,
    ) -> Any:
        if response.status_code == 204 or not response.content:
            return None
        try:
            payload = response.json()
        except ValueError as exc:
            raise EquipmentUnreachable(
                self._entry.id, f"{url} did not return JSON: {exc}"
            ) from exc

        if response_schema is None:
            return payload
        try:
            return response_schema.model_validate(payload)
        except ValidationError as exc:
            raise EquipmentUnreachable(
                self._entry.id,
                f"{url} response body does not match the expected schema: {exc}",
            ) from exc

    async def _fetch_envelope(
        self,
        path: str,
        model: type,
        *,
        label: str,
    ):
        url = self._url(path)
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
