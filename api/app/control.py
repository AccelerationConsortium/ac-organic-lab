"""Control passthrough router.

Equipment that conforms to STATUS_SPEC v1.0+ exposes its mutating endpoints
under ``POST /control/*`` (and ``DELETE /control/{...}``). The dashboard
mirrors that surface so the browser only ever talks to one origin (the
dashboard host) and so the camera/plug gateway never has to be exposed
to the tailnet directly.

Endpoint shape:

* ``POST   /api/equipment/{id}/control/{action}``
* ``DELETE /api/equipment/{id}/control/{action}``

Where ``{action}`` may include slashes (``preset/save``, ``preset/{id}``).
We deduce the gateway URL from the registry: the equipment entry's
``status_path`` ends in ``/status``; replacing that suffix with
``/control/{action}`` gives the right URL on the gateway. Adding a new
control surface to a device repo therefore needs no code change here.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger("ac_dashboard.api.control")


# How long we'll wait for a control call to complete. Most actions
# (PTZ nudge, plug toggle) finish in < 200ms; presets save can take a
# couple of seconds because the camera persists to flash.
_CONTROL_TIMEOUT_SECONDS = 6.0


def _control_url(base_url: str, status_path: str, action: str) -> str:
    """Compose the gateway URL for a control action.

    >>> _control_url("http://127.0.0.1:8002",
    ...              "/cameras/cam_lab499_west/status", "ptz")
    'http://127.0.0.1:8002/cameras/cam_lab499_west/control/ptz'
    """

    base = base_url.rstrip("/")
    if not status_path.endswith("/status"):
        raise HTTPException(
            status_code=503,
            detail=(
                f"Cannot derive control URL: status_path={status_path!r} "
                "does not end in '/status'"
            ),
        )
    prefix = status_path[: -len("/status")]
    suffix = action.lstrip("/")
    return f"{base}{prefix}/control/{suffix}"


def _device_url(base_url: str, status_path: str, sub: str) -> str:
    """Compose a non-control URL on the gateway, sibling to ``/status``.

    Used by the media passthrough: ``status_path`` for a camera is
    ``/cameras/<id>/status``, and the same ``/cameras/<id>`` namespace
    serves ``/media`` and ``/media/<kind>/<lens>/<name>``. Strip off the
    trailing ``/status`` and append ``sub``.

    >>> _device_url("http://127.0.0.1:8002",
    ...             "/cameras/cam_lab499_west/status", "media")
    'http://127.0.0.1:8002/cameras/cam_lab499_west/media'
    """

    base = base_url.rstrip("/")
    if not status_path.endswith("/status"):
        raise HTTPException(
            status_code=503,
            detail=(
                f"Cannot derive device URL: status_path={status_path!r} "
                "does not end in '/status'"
            ),
        )
    prefix = status_path[: -len("/status")]
    suffix = sub.lstrip("/")
    return f"{base}{prefix}/{suffix}"


def _has_control_capability(entry: Any) -> bool:
    """Cheap precondition guard.

    The aggregator marks ``http`` adapters as control-capable; ``mock`` and
    ``legacy_http`` adapters are not (yet). We refuse the call up-front
    rather than letting it 404 against a legacy gateway.
    """

    return getattr(entry, "adapter", None) == "http" and bool(getattr(entry, "base_url", None))


def build_control_router() -> APIRouter:
    router = APIRouter(prefix="/api/equipment", tags=["equipment-control"])

    @router.post("/{equipment_id}/control/{action:path}")
    async def control_post(
        equipment_id: str,
        action: str,
        request: Request,
        body: dict | None = None,
    ) -> dict:
        return await _proxy(request, equipment_id, action, "POST", body)

    @router.delete("/{equipment_id}/control/{action:path}")
    async def control_delete(
        equipment_id: str,
        action: str,
        request: Request,
    ) -> dict:
        return await _proxy(request, equipment_id, action, "DELETE", None)

    @router.get("/{equipment_id}/media")
    async def media_list(equipment_id: str, request: Request) -> dict:
        """List snapshots/recordings on the gateway for a camera."""

        return await _media_proxy_json(request, equipment_id, "media")

    @router.get("/{equipment_id}/media/{rest:path}")
    async def media_download(
        equipment_id: str, rest: str, request: Request
    ) -> StreamingResponse:
        """Stream a saved snapshot/recording back from the gateway.

        The browser hits e.g.
        ``/api/equipment/cam_hte_tapo_c245/media/snapshots/wide/2026-...jpg``
        and we forward to
        ``<gateway>/cameras/cam_hte_tapo_c245/media/snapshots/wide/2026-...jpg``,
        streaming the bytes through unmodified. Useful for the minimal
        gallery page and direct ``<img src=...>`` / ``<a href=...>``
        embedding without exposing the gateway to the LAN.
        """

        return await _media_proxy_stream(request, equipment_id, f"media/{rest}")

    return router


async def _proxy(
    request: Request,
    equipment_id: str,
    action: str,
    method: str,
    body: dict | None,
) -> dict:
    aggregator = getattr(request.app.state, "aggregator", None)
    if aggregator is None:
        raise HTTPException(status_code=503, detail="Aggregator not initialised")
    entry = aggregator.entry(equipment_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown equipment id: {equipment_id}")
    if not _has_control_capability(entry):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Equipment {equipment_id!r} does not expose a control surface"
                f" (adapter={entry.adapter})"
            ),
        )

    target = _control_url(entry.base_url, entry.status_path, action)
    timeout = httpx.Timeout(_CONTROL_TIMEOUT_SECONDS)
    # ``trust_env=False`` opts out of HTTP_PROXY / HTTPS_PROXY env vars.
    # Equipment ``base_url`` always points at a tailnet / loopback host
    # we control, so routing those calls through a corporate or local
    # dev proxy (e.g. Cursor's :51503) just causes 4xx/timeouts. The
    # aggregator follows the same convention.
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            if method == "POST":
                response = await client.post(target, json=body or {})
            elif method == "DELETE":
                response = await client.delete(target)
            else:  # pragma: no cover - guarded by FastAPI routing
                raise HTTPException(status_code=405, detail=f"Unsupported method: {method}")
    except httpx.TimeoutException as exc:
        logger.warning("control timeout %s %s -> %s: %s", method, equipment_id, target, exc)
        raise HTTPException(status_code=504, detail=f"Gateway timeout calling {target}") from exc
    except httpx.HTTPError as exc:
        logger.warning("control transport error %s %s -> %s: %s", method, equipment_id, target, exc)
        raise HTTPException(status_code=502, detail=f"Cannot reach gateway: {exc}") from exc

    # Forward the gateway's status code; many control endpoints return 4xx
    # to mean "this device cannot honour your request right now" (e.g. ONVIF
    # not configured) rather than a transport error.
    if response.status_code >= 400:
        try:
            payload = response.json()
            detail = payload.get("detail", payload)
        except ValueError:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail)

    try:
        return response.json()
    except ValueError:
        return {"ok": True, "raw": response.text}


async def _media_proxy_json(
    request: Request, equipment_id: str, sub: str
) -> dict:
    """JSON-only GET passthrough to ``<gateway>/<device>/<sub>``."""

    aggregator = getattr(request.app.state, "aggregator", None)
    if aggregator is None:
        raise HTTPException(status_code=503, detail="Aggregator not initialised")
    entry = aggregator.entry(equipment_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown equipment id: {equipment_id}")
    if not _has_control_capability(entry):
        raise HTTPException(
            status_code=400,
            detail=f"Equipment {equipment_id!r} has no gateway base_url",
        )

    target = _device_url(entry.base_url, entry.status_path, sub)
    timeout = httpx.Timeout(_CONTROL_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.get(target)
    except httpx.HTTPError as exc:
        logger.warning("media list error %s -> %s: %s", equipment_id, target, exc)
        raise HTTPException(status_code=502, detail=f"Cannot reach gateway: {exc}") from exc

    if response.status_code >= 400:
        try:
            payload = response.json()
            detail = payload.get("detail", payload)
        except ValueError:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail)
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


async def _media_proxy_stream(
    request: Request, equipment_id: str, sub: str
) -> StreamingResponse:
    """Streaming binary GET passthrough (snapshots / recordings).

    We deliberately avoid loading the whole file into memory - large
    recordings can be hundreds of MB - by using ``client.stream`` and
    relaying the iterator. Content-Type and Content-Length are forwarded
    from the gateway response headers.
    """

    aggregator = getattr(request.app.state, "aggregator", None)
    if aggregator is None:
        raise HTTPException(status_code=503, detail="Aggregator not initialised")
    entry = aggregator.entry(equipment_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown equipment id: {equipment_id}")
    if not _has_control_capability(entry):
        raise HTTPException(
            status_code=400,
            detail=f"Equipment {equipment_id!r} has no gateway base_url",
        )

    target = _device_url(entry.base_url, entry.status_path, sub)
    # Generous timeout for big recordings; the actual end-to-end transfer
    # time is bounded by the file size, not by us.
    timeout = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0)
    client = httpx.AsyncClient(timeout=timeout, trust_env=False)

    try:
        req = client.build_request("GET", target)
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning("media stream error %s -> %s: %s", equipment_id, target, exc)
        raise HTTPException(status_code=502, detail=f"Cannot reach gateway: {exc}") from exc

    if upstream.status_code >= 400:
        body = (await upstream.aread()).decode("utf-8", errors="replace")
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=upstream.status_code, detail=body[:400] or "media error")

    # Hop-by-hop / encoding headers must not be forwarded; we let
    # FastAPI/Starlette re-encode the body chunk stream we surface.
    skip = {"content-encoding", "transfer-encoding", "connection"}
    headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in skip
    }

    async def iter_body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        iter_body(),
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )


__all__ = ["build_control_router"]
