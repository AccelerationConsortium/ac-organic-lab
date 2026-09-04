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
import time
from typing import Any, Awaitable, Callable

import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .events import CONTROL_ACTION

logger = logging.getLogger("ac_dashboard.api.control")


# How long we'll wait for a control call to complete. Most actions
# (PTZ nudge, plug toggle) finish in < 200ms; presets save can take a
# couple of seconds because the camera persists to flash. The press
# (filter_every_well) blocks for its `hold_time` parameter, which the
# device caps at 10 s — budget is set above that with slack.
_CONTROL_TIMEOUT_SECONDS = 15.0
# Cytation reads and autofocus imaging are synchronous device calls. The live
# skill catalog budgets up to 30 s, so the dashboard needs a wider request
# window while it keeps the per-action claim alive below.
_PLATE_READER_CONTROL_TIMEOUT_SECONDS = 90.0
# The xArm's graph moves block until the motion completes: single hops measure
# 5-25 s live (audited plan runs hit 13.4 s and 23.2 s against the old 15 s
# budget), and graph/travel_to blocks for a whole multi-hop journey. The
# per-action claim heartbeat below keeps the claim alive throughout.
_ROBOT_ARM_CONTROL_TIMEOUT_SECONDS = 180.0
# The solid doser (dose_every_well) blocks for the whole action. Loader moves
# are ~3 s, but startup brings up gantry + doser + balance (six audited 504s
# at the 15 s default, the device still initialising at 16.6 s),
# calibrate.flow_rate dispenses for its `duration`, and a dose is ~15 s per
# well — dose.multiple at the assistant's 6-well cap
# (assistant_control._ARG_CARDINALITY_LIMITS) nominally needs 90 s. Kept
# under the Next.js proxy's 130 s cap (web/next.config proxyTimeout) so a
# slow action fails here — claim released, audit row written — not at the
# proxy.
_SOLID_DOSER_CONTROL_TIMEOUT_SECONDS = 120.0
_CLAIM_HEARTBEAT_FAILURE_BUDGET = 3


class _ClaimHeartbeatError(RuntimeError):
    """The dashboard could no longer prove its device claim was alive."""


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

    @router.get("/{equipment_id}/control/{action:path}")
    async def control_get(
        equipment_id: str,
        action: str,
        request: Request,
    ) -> dict:
        """Read-only control-namespace endpoints (e.g. ``read-balance``).

        These never carry ``X-Claim-Token`` (``_proxy`` only attempts the
        claim dance for POST), matching devices that expose them without
        ``Depends(require_claim)``.
        """
        return await _proxy(request, equipment_id, action, "GET", None)

    @router.delete("/{equipment_id}/control/{action:path}")
    async def control_delete(
        equipment_id: str,
        action: str,
        request: Request,
    ) -> dict:
        return await _proxy(request, equipment_id, action, "DELETE", None)

    @router.post("/{equipment_id}/device/{action:path}")
    async def device_post(
        equipment_id: str,
        action: str,
        request: Request,
        body: dict | None = None,
    ) -> dict:
        """Proxy to a device's root-level, claim-exempt "safety-floor" action.

        The xArm exposes connect / disconnect / move-stop / clear-errors as
        siblings of ``/status`` (outside ``/control/*``) and takes no claim on
        them, so the ``/control/*`` passthrough can't reach them. This route
        does: auth (via the same edge gate + ``_authorize_control``) and audit
        still apply — only the claim dance is skipped. Allowlisted per kind so
        it can't become a general side-door. See ``_device_action_proxy``.
        """
        return await _device_action_proxy(request, equipment_id, action, body)

    @router.get("/{equipment_id}/plate/{sub:path}")
    async def plate_get(equipment_id: str, sub: str, request: Request) -> Any:
        """JSON GET passthrough to ``<gateway>/plate/<sub>``.

        Sibling namespace to ``/control/*`` — dose_every_well's
        ``plate/status`` and ``plate/definitions`` are read-only and not
        claim-gated, so they don't go through ``_proxy``'s claim dance;
        this reuses the same generic JSON-GET plumbing as the camera media
        listing below. Return type is ``Any`` (not ``dict``) because
        ``plate/definitions`` returns a JSON array, not an object.
        """

        return await _media_proxy_json(request, equipment_id, f"plate/{sub}")

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


# Operator-identity forwarding to login-gated devices. Devices on the
# single-edge SSO standard (e.g. the xArm: `require_login` on
# connect/disconnect/stop/clear, `XARM_REQUIRE_LOGIN_FOR_CLAIM` on
# `/control/claim`) verify identity against the SAME ac_auth sidecar the
# dashboard uses, accepting (in order): trusted-edge headers, an `X-Api-Key`,
# or the `ac_auth_session` cookie (see xarm_api_server `_resolve_identity`).
#
# The dashboard reaches devices on their Tailnet base_url directly — not
# through the Caddy edge — so it forwards the operator's OWN credential from
# the incoming browser request: the session cookie (humans) or the api key
# (machine principals). The device verifies it with the sidecar itself, so no
# shared secret needs distributing and the device audits the real actor.
#
# Optional fast path: when DEVICE_EDGE_SHARED_SECRET is set (matching the
# device's XARM_EDGE_SHARED_SECRET), present the already-verified identity as
# trusted-edge headers instead — header-only on the device, no second sidecar
# round-trip. Purely an optimisation; cookie forwarding is the default.
_EDGE_SHARED_SECRET = os.environ.get("DEVICE_EDGE_SHARED_SECRET", "").strip()
_AUTH_COOKIE_NAME = os.environ.get("AUTH_COOKIE_NAME", "ac_auth_session")


# Device replies that mean "I do not know who you are" — i.e. the credential
# we presented was not accepted. 401 only: a 403 means the device *did*
# identify the caller and refused them, which another credential should not
# be used to work around.
_AUTH_FALLBACK_STATUS = frozenset({401})


def _edge_secret_for(entry: Any | None) -> str:
    """The edge shared secret to present to *this* device.

    Per-device by design: each device behind the edge trusts a different
    secret, so one dashboard-wide value can satisfy at most one of them. The
    registry entry names the environment variable
    (``edge_secret_env: XARM_EDGE_SHARED_SECRET``) and the value is read from
    this process's environment, so nothing secret is committed.

    Falls back to the global ``DEVICE_EDGE_SHARED_SECRET`` when the entry names
    no variable or the named one is unset — the pre-2026-08-12 behaviour, kept
    so devices that were never given an entry keep working exactly as before.
    """

    name = getattr(entry, "edge_secret_env", None) if entry is not None else None
    if name:
        value = os.environ.get(name, "").strip()
        if value:
            return value
        # Named but absent: fall through to the global rather than sending
        # nothing, and say so once — a typo'd variable name is otherwise
        # indistinguishable from a device that simply does not gate.
        logger.warning(
            "edge_secret_env=%s is set on %s but that variable is empty; "
            "falling back to DEVICE_EDGE_SHARED_SECRET",
            name, getattr(entry, "id", "?"),
        )
    return _EDGE_SHARED_SECRET


def _device_auth_candidates(
    request: Request, entry: Any | None = None
) -> list[dict[str, str]]:
    """Identity headers to try against a login-gated device, best first.

    Two ways to prove who the operator is, and a device may accept either:

    1. **Trusted-edge fast path** — present the already-verified identity as
       ``X-Auth-User`` + ``X-Edge-Auth``. Header-only on the device, no second
       sidecar round-trip. Requires the device to share the secret
       (``XARM_EDGE_SHARED_SECRET`` and friends).
    2. **The operator's own credential** — their machine api key, else the
       ac_auth session cookie (only that cookie, never the full jar).

    Both are returned, in that order, because a device that does not implement
    the fast path answers **401** to it — and before this returned a list, the
    fast path was tried *exclusively* whenever the shared secret happened to be
    set, so the operator credential below it was unreachable in production.
    Every dashboard-mediated control action against such a device failed 401:
    tiles, the workflow executor, and the assistant's Authorize button alike.
    Trying the next candidate on 401 makes the fast path what its comment
    always claimed it was — an optimisation, not a commitment.

    The list is never empty: with no credential at all it is ``[{}]``, one
    unauthenticated attempt, which is what an open dev deployment or a device
    with login disabled expects.
    """

    candidates: list[dict[str, str]] = []
    edge_secret = _edge_secret_for(entry)
    if edge_secret:
        user = request.headers.get("x-auth-user")
        if user:
            headers = {"X-Auth-User": user, "X-Edge-Auth": edge_secret}
            role = request.headers.get("x-auth-role")
            if role:
                headers["X-Auth-Role"] = role
            candidates.append(headers)
    api_key = request.headers.get("x-api-key")
    if api_key:
        candidates.append({"X-Api-Key": api_key})
    token = request.cookies.get(_AUTH_COOKIE_NAME)
    if token:
        candidates.append({"Cookie": f"{_AUTH_COOKIE_NAME}={token}"})
    return candidates or [{}]


def _device_auth_headers(
    request: Request, entry: Any | None = None
) -> dict[str, str]:
    """The preferred identity headers — first of :func:`_device_auth_candidates`.

    Kept for callers that issue a single hop and cannot retry (``workflow.py``),
    which inherit the pre-fallback behaviour. ``workflow.py`` also passes no
    entry: it builds **one** header set for a whole multi-device run, so it
    cannot do per-device secrets without threading the target through
    ``lab_skills``. It therefore gets the global fallback, and a run touching a
    device with its own secret still fails closed at that step.
    """

    return _device_auth_candidates(request, entry)[0]


async def _acquire_claim_with_fallback(
    client: httpx.AsyncClient,
    base_url: str,
    status_path: str,
    equipment_id: str,
    owner: str,
    candidates: list[dict[str, str]],
) -> tuple[str, float, dict[str, str]]:
    """Claim, trying each credential until one is not rejected as unknown.

    Returns ``(claim_token, heartbeat_interval_s, headers_that_worked)`` — the
    caller reuses the credentials for heartbeat, action, and release, so one
    sequence never mixes identities.
    """

    for index, headers in enumerate(candidates):
        try:
            token, heartbeat_interval_s = await _acquire_claim(
                client, base_url, status_path, equipment_id, owner,
                extra_headers=headers or None,
            )
            return token, heartbeat_interval_s, headers
        except HTTPException as exc:
            if (
                exc.status_code not in _AUTH_FALLBACK_STATUS
                or index + 1 >= len(candidates)
            ):
                raise
            logger.info(
                "claim on %s rejected credential %d/%d (401); trying the next",
                equipment_id, index + 1, len(candidates),
            )
    raise AssertionError("unreachable: candidates is never empty")  # pragma: no cover


async def _send_with_auth_fallback(
    send: Callable[[dict[str, str] | None], Awaitable[httpx.Response]],
    candidates: list[dict[str, str]],
) -> tuple[httpx.Response, dict[str, str]]:
    """Same fallback for a lone hop that carries no claim.

    ``send`` must be safe to repeat: it is only re-issued when the device
    answered 401, which means it refused to act on the first attempt.
    """

    for index, headers in enumerate(candidates):
        response = await send(headers or None)
        if (
            response.status_code in _AUTH_FALLBACK_STATUS
            and index + 1 < len(candidates)
        ):
            logger.info(
                "device rejected credential %d/%d (401); trying the next",
                index + 1, len(candidates),
            )
            continue
        return response, headers
    raise AssertionError("unreachable: candidates is never empty")  # pragma: no cover


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
    extra_headers: dict[str, str] | None = None,
) -> tuple[str, float]:
    """POST /control/claim and return its token and heartbeat interval.

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
        resp = await client.post(claim_url, json=body, headers=extra_headers or None)
    except httpx.HTTPError as exc:
        logger.warning("claim transport error %s -> %s: %s", equipment_id, claim_url, exc)
        raise HTTPException(status_code=502, detail=f"Cannot acquire claim: {exc}") from exc

    if resp.status_code == 200:
        try:
            payload = resp.json()
            token = str(payload["claim_token"])
            heartbeat_interval_s = float(payload["heartbeat_interval_s"])
            if heartbeat_interval_s <= 0:
                raise ValueError("heartbeat_interval_s must be positive")
            return token, heartbeat_interval_s
        except (TypeError, ValueError, KeyError) as exc:
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


async def _heartbeat_claim(
    client: httpx.AsyncClient,
    base_url: str,
    status_path: str,
    token: str,
    equipment_id: str,
    interval_s: float,
    extra_headers: dict[str, str] | None = None,
) -> None:
    """Renew a dashboard claim until cancelled or the failure budget is spent."""

    heartbeat_url = _control_url(base_url, status_path, "heartbeat")
    consecutive_failures = 0
    last_error = ""
    while True:
        await asyncio.sleep(interval_s)
        try:
            response = await client.post(
                heartbeat_url,
                headers={"X-Claim-Token": token, **(extra_headers or {})},
            )
            if response.status_code >= 400:
                raise _ClaimHeartbeatError(
                    f"heartbeat returned HTTP {response.status_code}"
                )
        except (httpx.HTTPError, _ClaimHeartbeatError) as exc:
            consecutive_failures += 1
            last_error = str(exc)
            if consecutive_failures >= _CLAIM_HEARTBEAT_FAILURE_BUDGET:
                raise _ClaimHeartbeatError(
                    f"claim heartbeat failed for {equipment_id}: {last_error}"
                ) from exc
        else:
            consecutive_failures = 0


async def _release_claim_best_effort(
    client: httpx.AsyncClient,
    base_url: str,
    status_path: str,
    token: str,
    equipment_id: str,
    extra_headers: dict[str, str] | None = None,
) -> None:
    """POST /control/release; swallow errors. Idempotent per the spec."""

    try:
        release_url = _control_url(base_url, status_path, "release")
        await client.post(
            release_url, headers={"X-Claim-Token": token, **(extra_headers or {})}
        )
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
    duration_s: float | None = None,
) -> None:
    """Append one audit row to ``equipment_events`` for a control action.

    The dashboard now *writes* to devices (per-request claims), so every
    mutating passthrough call is recorded with the actor (``owner``), the
    action, and the outcome (``ok`` / ``refused`` / ``claim_denied`` /
    ``claim_lost`` / ``timeout`` / ``transport_error``).

    ``duration_s`` is the wall-clock of the device interaction (the full
    claim → action → release dance for v1.1 devices) as seen from the
    dashboard. Many device endpoints block until the physical operation
    completes, so this recovers an operation-duration record that the
    poll-sampled activity series cannot (STATUS_SPEC §2.3.1) — but only for
    dashboard-originated calls; SDK calls straight to the device remain
    invisible here. ``None`` when no device interaction happened (e.g.
    authorization refused before the first hop).

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
    if duration_s is not None:
        payload["duration_s"] = round(duration_s, 3)
    if detail is not None:
        payload["detail"] = detail[:500] if isinstance(detail, str) else detail
    # Provenance of the click. Tile controls omit this header; the lab
    # assistant's Authorize button stamps `X-Control-Origin: assistant`
    # (UI_DESIGN §5), so the audit trail separates assistant-originated actions
    # from direct operator clicks. Recorded on the row regardless of outcome.
    origin = request.headers.get("x-control-origin")
    if origin:
        payload["origin"] = origin[:40]
    # Step 1i: a step of an assistant-proposed plan also names the plan it
    # belongs to (`<plan_id>#<step index>`), so the per-step rows can be
    # joined back to the `assistant_plan_approved` row that reviewed them.
    plan_ref = request.headers.get("x-control-plan")
    if plan_ref:
        payload["plan"] = plan_ref[:80]
    message = f"{owner} {method} {action} → {outcome} ({status_code})"
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            functools.partial(
                db.record_equipment_event,
                equipment_id,
                CONTROL_ACTION,
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
    # Any protocol ≥ 1.1 has claim semantics (v1.2 is additive over v1.1 —
    # a "1.2" device still hard-enforces X-Claim-Token). Only v1.0 devices
    # skip the dance.
    needs_claim = (
        getattr(entry, "protocol", None) not in (None, "1.0")
        and method == "POST"
        and action not in _CLAIM_PROTOCOL_ACTIONS
    )

    # Shared, long-lived httpx client (configured in main.lifespan with
    # ``trust_env=False`` and a 15 s default timeout). Cytation overrides the
    # action request to 90 s because reads and autofocus imaging are synchronous.
    # Re-using one client
    # is what unlocks HTTP/1.1 keep-alive — for v1.1 devices the three
    # round-trips (claim → action → release) share one warm socket
    # instead of paying TCP handshake × 3 per click.
    owner = _claim_owner(request)
    client = _get_control_client(request)
    # Authorization precedes the claim dance: an unauthorized caller must
    # never even acquire a claim (claiming is itself control).
    await _authorize_control(request, client, equipment_id, action, method, owner)
    # Operator credential forwarded on every device hop (claim, action,
    # release) so login-gated claims (XARM_REQUIRE_LOGIN_FOR_CLAIM) pass and
    # the device stamps/audits the real operator. Empty when unauthenticated.
    auth_candidates = _device_auth_candidates(request, entry)
    edge_headers = auth_candidates[0]
    action_timeout = {
        "plate_reader": _PLATE_READER_CONTROL_TIMEOUT_SECONDS,
        "robot_arm": _ROBOT_ARM_CONTROL_TIMEOUT_SECONDS,
        "solid_doser": _SOLID_DOSER_CONTROL_TIMEOUT_SECONDS,
    }.get(getattr(entry, "kind", None) or "", _CONTROL_TIMEOUT_SECONDS)
    # Wall-clock of the whole device interaction (claim → action → release),
    # stamped into the audit payload as `duration_s`. Started here — after
    # auth, before the first device hop — so refusals that never reach the
    # device carry no duration.
    started = time.monotonic()
    try:
        async def _send(headers: dict[str, str] | None) -> httpx.Response:
            if method == "POST":
                return await client.post(
                    target,
                    json=body or {},
                    headers=headers,
                    timeout=action_timeout,
                )
            if method == "GET":
                return await client.get(
                    target, headers=headers, timeout=action_timeout
                )
            if method == "DELETE":
                return await client.delete(
                    target, headers=headers, timeout=action_timeout
                )
            # pragma: no cover - guarded by FastAPI routing
            raise HTTPException(status_code=405, detail=f"Unsupported method: {method}")

        token: str | None = None
        heartbeat_task: asyncio.Task[None] | None = None
        heartbeat_error: _ClaimHeartbeatError | None = None
        if needs_claim:
            # The claim is the first hop, so it is where the credential gets
            # settled; the action and release below reuse whatever worked.
            token, heartbeat_interval_s, edge_headers = await _acquire_claim_with_fallback(
                client, entry.base_url, entry.status_path, equipment_id, owner,
                auth_candidates,
            )
            heartbeat_task = asyncio.create_task(
                _heartbeat_claim(
                    client,
                    entry.base_url,
                    entry.status_path,
                    token,
                    equipment_id,
                    heartbeat_interval_s,
                    extra_headers=edge_headers,
                ),
                name=f"dashboard-claim-heartbeat-{equipment_id}",
            )
        try:
            if token is not None:
                response = await _send({**edge_headers, "X-Claim-Token": token})
            else:
                # No claim on this device, so the action itself is the first
                # hop and carries the fallback.
                response, edge_headers = await _send_with_auth_fallback(
                    _send, auth_candidates
                )
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                except _ClaimHeartbeatError as exc:
                    heartbeat_error = exc
            if token is not None:
                await _release_claim_best_effort(
                    client, entry.base_url, entry.status_path, token, equipment_id,
                    extra_headers=edge_headers,
                )
        if heartbeat_error is not None:
            raise heartbeat_error
    except _ClaimHeartbeatError as exc:
        # Unlike a claim denial, this happens after the action returned. Say so
        # explicitly: the physical operation may have completed, but the
        # dashboard cannot report a claim-protected sequence as successful.
        detail = (
            "Device action returned, but the dashboard lost its claim "
            f"heartbeat: {exc}"
        )
        logger.warning("control claim lost %s %s: %s", method, equipment_id, exc)
        await _record_control_event(
            request, equipment_id, action, method,
            owner=owner, status_code=502,
            outcome="claim_lost", detail=detail,
            duration_s=time.monotonic() - started,
        )
        raise HTTPException(status_code=502, detail=detail) from exc
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
            duration_s=time.monotonic() - started,
        )
        raise
    except httpx.TimeoutException as exc:
        logger.warning("control timeout %s %s -> %s: %s", method, equipment_id, target, exc)
        await _record_control_event(
            request, equipment_id, action, method,
            owner=owner, status_code=504,
            outcome="timeout", detail=str(exc),
            duration_s=time.monotonic() - started,
        )
        raise HTTPException(status_code=504, detail=f"Gateway timeout calling {target}") from exc
    except httpx.HTTPError as exc:
        logger.warning("control transport error %s %s -> %s: %s", method, equipment_id, target, exc)
        await _record_control_event(
            request, equipment_id, action, method,
            owner=owner, status_code=502,
            outcome="transport_error", detail=str(exc),
            duration_s=time.monotonic() - started,
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
            duration_s=time.monotonic() - started,
        )
        raise HTTPException(status_code=response.status_code, detail=detail)

    await _record_control_event(
        request, equipment_id, action, method,
        owner=owner, status_code=response.status_code,
        outcome="ok",
        duration_s=time.monotonic() - started,
    )
    try:
        return response.json()
    except ValueError:
        return {"ok": True, "raw": response.text}


# Root-level device actions that live OUTSIDE the ``/control/*`` namespace:
# the device's claim-exempt "safety floor". The ``/control/*`` passthrough
# can't reach them (they're siblings of ``/status``, not under ``/control/``)
# and they take no claim. We still require auth (the Next middleware gates the
# path; ``_authorize_control`` enforces the role) and still audit. Allowlisted
# per kind so this cannot become a general side-door into arbitrary paths.
#
# TODO(xarm): retire once the device exposes ``/control/*`` aliases for these
# and the tile can go back through the standard claim-gated passthrough.
_DEVICE_ACTION_ALLOWLIST: dict[str, frozenset[str]] = {
    "robot_arm": frozenset({"connect", "disconnect", "move/stop", "clear/errors"}),
}


async def _device_action_proxy(
    request: Request,
    equipment_id: str,
    action: str,
    body: dict | None,
) -> dict:
    """Auth + audit proxy to an allowlisted root-level device action.

    Unlike :func:`_proxy` this performs no claim dance: connect is claim-gated
    on the device side (you can't claim until connected) and stop/clear are the
    safety floor. Auth is *not* skipped — the edge middleware already rejects
    unauthenticated callers, and ``_authorize_control`` enforces the per-device
    role when an identity is present.
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
            detail=(
                f"Equipment {equipment_id!r} does not expose a control surface"
                f" (adapter={entry.adapter})"
            ),
        )

    normalized = action.strip("/")
    allowed = _DEVICE_ACTION_ALLOWLIST.get(getattr(entry, "kind", None), frozenset())
    if normalized not in allowed:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Device action {normalized!r} is not allowlisted for kind "
                f"{getattr(entry, 'kind', None)!r}"
            ),
        )

    owner = _claim_owner(request)
    client = _get_control_client(request)
    # Auth precedes the call (claim-exempt on the device ≠ auth-exempt here).
    await _authorize_control(request, client, equipment_id, normalized, "POST", owner)

    target = _device_url(entry.base_url, entry.status_path, normalized)
    # Forward the operator's credential so the device's `require_login` passes
    # and its audit records the real actor (empty when unauthenticated).
    auth_candidates = _device_auth_candidates(request, entry)
    started = time.monotonic()
    try:
        response, _used = await _send_with_auth_fallback(
            lambda headers: client.post(target, json=body or {}, headers=headers),
            auth_candidates,
        )
    except httpx.TimeoutException as exc:
        logger.warning("device action timeout %s -> %s: %s", equipment_id, target, exc)
        await _record_control_event(
            request, equipment_id, normalized, "POST",
            owner=owner, status_code=504, outcome="timeout", detail=str(exc),
            duration_s=time.monotonic() - started,
        )
        raise HTTPException(status_code=504, detail=f"Gateway timeout calling {target}") from exc
    except httpx.HTTPError as exc:
        logger.warning("device action transport error %s -> %s: %s", equipment_id, target, exc)
        await _record_control_event(
            request, equipment_id, normalized, "POST",
            owner=owner, status_code=502, outcome="transport_error", detail=str(exc),
            duration_s=time.monotonic() - started,
        )
        raise HTTPException(status_code=502, detail=f"Cannot reach gateway: {exc}") from exc

    if response.status_code >= 400:
        try:
            payload = response.json()
            detail = payload.get("detail", payload)
        except ValueError:
            detail = response.text
        await _record_control_event(
            request, equipment_id, normalized, "POST",
            owner=owner, status_code=response.status_code, outcome="refused", detail=detail,
            duration_s=time.monotonic() - started,
        )
        raise HTTPException(status_code=response.status_code, detail=detail)

    await _record_control_event(
        request, equipment_id, normalized, "POST",
        owner=owner, status_code=response.status_code, outcome="ok",
        duration_s=time.monotonic() - started,
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
