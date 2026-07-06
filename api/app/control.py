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

import asyncio
import functools
import logging
import os
from typing import Any

import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger("ac_dashboard.api.control")


# How long we'll wait for a control call to complete. Most actions
# (PTZ nudge, plug toggle) finish in < 200ms; presets save can take a
# couple of seconds because the camera persists to flash. The press
# (filter_every_well) blocks for its `hold_time` parameter, which the
# device caps at 10 s — budget is set above that with slack.
_CONTROL_TIMEOUT_SECONDS = 15.0


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


def _get_control_client(request: Request) -> httpx.AsyncClient:
    """Return the app-wide shared :class:`httpx.AsyncClient`.

    Configured in :func:`main.lifespan`; if it's missing we're being
    invoked outside the normal app context (a test harness or a
    misconfigured deployment) — fall back to a one-shot client so the
    handler degrades to the previous behaviour instead of 500-ing.
    """

    client = getattr(request.app.state, "control_client", None)
    if client is None:
        # No lifespan-managed client; degrade to per-request behaviour.
        return httpx.AsyncClient(
            timeout=httpx.Timeout(_CONTROL_TIMEOUT_SECONDS),
            trust_env=False,
        )
    return client


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


# STATUS_SPEC v1.1 reserves three action names for the claim protocol
# itself. We must NOT wrap calls to these in another claim dance (it
# would loop or shadow the user's intent).
_CLAIM_PROTOCOL_ACTIONS: frozenset[str] = frozenset({"claim", "heartbeat", "release"})

# Fallback identity surfaced in `details.claimed_by` when no authenticated user
# is present (local/dev, or before the Caddy forward_auth edge is wired). When
# the public edge runs, it injects `X-Auth-User` (ac_auth `/auth/verify`); we
# stamp that real owner into the claim + audit instead — see `_claim_owner`.
# The device then resolves owner→role from its roster projection
# (`GET /equipment/{key}/roster`), so per-user device roles work end-to-end.
_DASHBOARD_CLAIM_OWNER = "ac-organic-lab-dashboard"


def _claim_owner(request: Request) -> str:
    """The actor to stamp into the device claim + audit row.

    Prefer the authenticated user injected by the edge (`X-Auth-User`, set by
    Caddy `forward_auth` → ac_auth). Fall back to the dashboard identity when
    unauthenticated (local/dev or pre-edge). Trusting the header is safe only
    because it arrives from the trusted edge, never from the public client
    directly (Caddy strips inbound X-Auth-* and re-injects the verified value)."""
    return request.headers.get("x-auth-user") or _DASHBOARD_CLAIM_OWNER

# Claim TTL. Long enough to cover the slowest device action (PlateLoc's
# seal cycle is ~8 s; press init is ~4 s) plus network slack. The device
# may clamp this to its own min/max - the response's `expires_at` is
# authoritative.
_CLAIM_TTL_SECONDS = 30.0


def _authz_base() -> str:
    """Auth sidecar base URL (same env the Next.js middleware uses)."""
    return os.environ.get("AUTH_SERVICE_BASE", "http://127.0.0.1:8009")


def _authz_enforced() -> bool:
    """Escape hatch mirroring DASHBOARD_CONTROL_OPEN's spirit: set
    CONTROL_AUTHZ_ENFORCE=false for local dev without the auth sidecar."""
    return os.environ.get("CONTROL_AUTHZ_ENFORCE", "true").lower() != "false"


async def _fetch_authz_verdict(
    client: httpx.AsyncClient, user: str, equipment_id: str
) -> dict:
    """One GET /authz/check against the sidecar. Seam kept separate so tests
    can monkeypatch it without a live sidecar."""
    resp = await client.get(
        f"{_authz_base()}/authz/check",
        params={"user": user, "equipment": equipment_id},
    )
    resp.raise_for_status()
    return resp.json()


async def _authorize_control(
    request: Request,
    client: httpx.AsyncClient,
    equipment_id: str,
    action: str,
    method: str,
    owner: str,
) -> None:
    """Per-equipment authorization at the gateway (pre-Phase-3 enforcement).

    When the request carries an authenticated identity (``X-Auth-User``,
    injected by the Next.js middleware / Caddy edge after verifying the
    session — never client-supplied), ask the auth sidecar whether that
    principal holds a role on this equipment and refuse with 403 if not.
    Unauthenticated requests skip the check: in production the middleware
    already rejects unauthenticated control, so no header here means a
    deliberately open deployment (dev) or the pre-edge fallback identity.

    Fail-closed when the sidecar is unreachable — control without
    authorization is worse than a stalled click. The denial/outage is
    audited like every other control outcome.
    """
    if not _authz_enforced():
        return
    user = request.headers.get("x-auth-user")
    if not user:
        return
    try:
        verdict = await _fetch_authz_verdict(client, user, equipment_id)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "authz check unavailable for %s on %s: %s", user, equipment_id, exc
        )
        await _record_control_event(
            request, equipment_id, action, method,
            owner=owner, status_code=503,
            outcome="authz_unavailable", detail=str(exc),
        )
        raise HTTPException(
            status_code=503,
            detail="Authorization service unavailable; control refused (fail-closed).",
        ) from exc
    if not verdict.get("allowed"):
        reason = str(verdict.get("reason") or "no role on this equipment")
        await _record_control_event(
            request, equipment_id, action, method,
            owner=owner, status_code=403,
            outcome="forbidden", detail=reason,
        )
        raise HTTPException(
            status_code=403,
            detail=f"{user} is not authorized to control {equipment_id}: {reason}",
        )


async def _acquire_claim(
    client: httpx.AsyncClient,
    base_url: str,
    status_path: str,
    equipment_id: str,
    owner: str,
) -> str:
    """POST /control/claim and return the claim token.

    ``owner`` is the authenticated actor (or dashboard fallback); the device
    records it in ``details.claimed_by.owner`` and resolves its role from the
    roster projection. Raises HTTPException on any non-200 (the device's status
    code and body are forwarded so callers see ``claimed_by`` / ``retry_after_s``).
    """

    claim_url = _control_url(base_url, status_path, "claim")
    body = {
        "owner": owner,
        "session_id": str(uuid.uuid4()),
        "ttl_s": _CLAIM_TTL_SECONDS,
    }
    try:
        resp = await client.post(claim_url, json=body)
    except httpx.HTTPError as exc:
        logger.warning("claim transport error %s -> %s: %s", equipment_id, claim_url, exc)
        raise HTTPException(status_code=502, detail=f"Cannot acquire claim: {exc}") from exc

    if resp.status_code == 200:
        try:
            return str(resp.json()["claim_token"])
        except (ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Malformed claim response from {claim_url}",
            ) from exc

    # 409 / 423 / 503 / 422 — surface the device's body verbatim so the
    # frontend modal can render `claimed_by.owner` and `retry_after_s`.
    try:
        detail = resp.json()
    except ValueError:
        detail = resp.text
    raise HTTPException(status_code=resp.status_code, detail=detail)


async def _release_claim_best_effort(
    client: httpx.AsyncClient,
    base_url: str,
    status_path: str,
    token: str,
    equipment_id: str,
) -> None:
    """POST /control/release; swallow errors. Idempotent per the spec."""

    try:
        release_url = _control_url(base_url, status_path, "release")
        await client.post(release_url, headers={"X-Claim-Token": token})
    except Exception as exc:  # noqa: BLE001 - best-effort cleanup
        logger.warning(
            "claim release failed %s -> %s: %s", equipment_id, release_url, exc
        )


# Audit identity for control. ``owner`` is resolved by `_claim_owner` — the
# authenticated `X-Auth-User` injected by the edge when present, else the
# dashboard fallback — and is the actor stamped into both the audit row and the
# device claim (`details.claimed_by.owner`). See AUTH_DESIGN.md.
async def _record_control_event(
    request: Request,
    equipment_id: str,
    action: str,
    method: str,
    *,
    owner: str,
    status_code: int,
    outcome: str,
    detail: Any = None,
) -> None:
    """Append one audit row to ``equipment_events`` for a control action.

    The dashboard now *writes* to devices (per-request claims), so every
    mutating passthrough call is recorded with the actor (``owner``), the
    action, and the outcome (``ok`` / ``refused`` / ``claim_denied`` /
    ``timeout`` / ``transport_error``).

    Best-effort and non-blocking: the synchronous sqlite write is pushed to
    a worker thread so the event loop never blocks, and any failure is
    logged then swallowed — auditing must never break a control call. A
    no-op when the history DB is unavailable (e.g. test harness, or DB
    failed to open at startup).
    """

    db = getattr(request.app.state, "db", None)
    if db is None:
        return
    payload: dict[str, Any] = {
        "action": action,
        "method": method,
        "status_code": status_code,
        "outcome": outcome,
        "owner": owner,
    }
    if detail is not None:
        payload["detail"] = detail[:500] if isinstance(detail, str) else detail
    message = f"{owner} {method} {action} → {outcome} ({status_code})"
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            functools.partial(
                db.record_equipment_event,
                equipment_id,
                "control_action",
                message=message,
                payload=payload,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - auditing must never break control
        logger.warning("audit write failed %s %s: %s", equipment_id, action, exc)


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

    # v1.1 devices may enforce X-Claim-Token on /control/*. We acquire a
    # short-lived claim per request, attach the token, then release in a
    # finally block. Calls to the claim protocol itself (claim/heartbeat/
    # release) are passed through unwrapped so callers that want to
    # manage their own claim can. v1.0 devices skip the dance entirely.
    needs_claim = (
        getattr(entry, "protocol", None) == "1.1"
        and method == "POST"
        and action not in _CLAIM_PROTOCOL_ACTIONS
    )

    # Shared, long-lived httpx client (configured in main.lifespan with
    # ``trust_env=False`` and a 15 s default timeout). Re-using one client
    # is what unlocks HTTP/1.1 keep-alive — for v1.1 devices the three
    # round-trips (claim → action → release) share one warm socket
    # instead of paying TCP handshake × 3 per click.
    owner = _claim_owner(request)
    client = _get_control_client(request)
    # Authorization precedes the claim dance: an unauthorized caller must
    # never even acquire a claim (claiming is itself control).
    await _authorize_control(request, client, equipment_id, action, method, owner)
    try:
        token: str | None = None
        if needs_claim:
            token = await _acquire_claim(
                client, entry.base_url, entry.status_path, equipment_id, owner
            )
        try:
            headers = {"X-Claim-Token": token} if token else None
            if method == "POST":
                response = await client.post(target, json=body or {}, headers=headers)
            elif method == "DELETE":
                response = await client.delete(target, headers=headers)
            else:  # pragma: no cover - guarded by FastAPI routing
                raise HTTPException(status_code=405, detail=f"Unsupported method: {method}")
        finally:
            if token is not None:
                await _release_claim_best_effort(
                    client, entry.base_url, entry.status_path, token, equipment_id
                )
    except HTTPException as exc:
        # The action never executed: claim acquisition was refused
        # (409/423/503/422) or the method was unsupported. Audit the
        # refusal, then re-raise so the frontend still sees the device's
        # verbatim body (claimed_by / retry_after_s).
        outcome = "claim_denied" if exc.status_code in (409, 423) else "refused"
        await _record_control_event(
            request, equipment_id, action, method,
            owner=owner, status_code=exc.status_code,
            outcome=outcome, detail=exc.detail,
        )
        raise
    except httpx.TimeoutException as exc:
        logger.warning("control timeout %s %s -> %s: %s", method, equipment_id, target, exc)
        await _record_control_event(
            request, equipment_id, action, method,
            owner=owner, status_code=504,
            outcome="timeout", detail=str(exc),
        )
        raise HTTPException(status_code=504, detail=f"Gateway timeout calling {target}") from exc
    except httpx.HTTPError as exc:
        logger.warning("control transport error %s %s -> %s: %s", method, equipment_id, target, exc)
        await _record_control_event(
            request, equipment_id, action, method,
            owner=owner, status_code=502,
            outcome="transport_error", detail=str(exc),
        )
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
        await _record_control_event(
            request, equipment_id, action, method,
            owner=owner, status_code=response.status_code,
            outcome="refused", detail=detail,
        )
        raise HTTPException(status_code=response.status_code, detail=detail)

    await _record_control_event(
        request, equipment_id, action, method,
        owner=owner, status_code=response.status_code,
        outcome="ok",
    )
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
    client = _get_control_client(request)
    try:
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
    client = _get_control_client(request)
    # Per-request timeout for big recordings; the client default (15 s)
    # would 504 on a 200 MB file. The actual end-to-end transfer time
    # is bounded by the file size, not by us.
    media_timeout = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0)

    try:
        req = client.build_request("GET", target, timeout=media_timeout)
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        logger.warning("media stream error %s -> %s: %s", equipment_id, target, exc)
        raise HTTPException(status_code=502, detail=f"Cannot reach gateway: {exc}") from exc

    if upstream.status_code >= 400:
        body = (await upstream.aread()).decode("utf-8", errors="replace")
        await upstream.aclose()
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
            # NB: don't close `client` — it's app-state shared and lives
            # for the process lifetime. Only the per-stream response is
            # closed here.
            await upstream.aclose()

    return StreamingResponse(
        iter_body(),
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )


__all__ = ["build_control_router"]
