"""SDL2-session-gated access to the read-only Robot Motion workspace.

This is deliberately not a robot control proxy. Adding physical control requires
its own reviewed SDK/claim integration, not widening this allowlist.
"""

from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse


_EQUIPMENT = "ligand_ur5e"
_MAX_REQUEST = 256 * 1024
_MAX_RESPONSE = 4 * 1024 * 1024
_TIMEOUT = 15.0
_GET_TYPES = {
    "status": {"application/json"},
    "drivers": {"application/json"},
    "graph": {"application/json"},
    "web/": {"text/html"},
    "web/index.html": {"text/html"},
    "web/main.js": {"text/javascript", "application/javascript"},
    "web/style.css": {"text/css"},
    "web/pyxarm/style.css": {"text/css"},
    "web/pyxarm/cytoscape.min.js": {"text/javascript", "application/javascript"},
}
_POST_PATHS = {"graph/validate", "graph/preview"}
_DOC_PATHS = {"docs", "agent-docs", "agent-docs/api-reference", "openapi.json", "llms.txt"}
_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data: blob:; font-src 'self'; "
        "frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
    ),
}


async def _authorize(request: Request, client: httpx.AsyncClient) -> None:
    cookie = request.headers.get("cookie")
    if not cookie:
        raise HTTPException(401, "Sign in with SDL2 to open the Robot Motion panel")
    base = os.environ.get("AUTH_SERVICE_BASE", "http://127.0.0.1:8009").rstrip("/")
    # Standalone requests deliberately exclude API keys, client defaults, and
    # caller-supplied identity headers. Only the sidecar establishes identity.
    try:
        verified = await client.send(httpx.Request(
            "GET", f"{base}/auth/verify",
            headers={"Cookie": cookie, "Accept": "application/json",
                     "X-Forwarded-Uri": request.url.path},
            extensions={"timeout": httpx.Timeout(5).as_dict()},
        ), auth=None, follow_redirects=False)
        if verified.status_code in {401, 403}:
            raise HTTPException(verified.status_code, "SDL2 sign-in is required or access was denied")
        if verified.status_code != 200 or not verified.headers.get("x-auth-user"):
            raise HTTPException(503, "SDL2 authentication unavailable")
        permission = await client.send(httpx.Request(
            "GET", f"{base}/authz/check",
            params={"user": verified.headers["x-auth-user"], "equipment": _EQUIPMENT},
            headers={"Accept": "application/json"},
            extensions={"timeout": httpx.Timeout(5).as_dict()},
        ), auth=None, follow_redirects=False)
        if permission.status_code != 200:
            raise HTTPException(503, "SDL2 authorization unavailable")
        grant = permission.json()
        if not isinstance(grant, dict):
            raise ValueError("Invalid authorization response")
        if grant.get("allowed") is not True or grant.get("central_role") == "automation":
            raise HTTPException(403, "Your SDL2 account does not have access to this panel")
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "SDL2 authentication unavailable") from exc


async def _body(request: Request) -> bytes:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "Graph requests require application/json")
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        raise HTTPException(415, "Compressed graph requests are not supported")
    # Browser JSON posts have Origin; reject cross-site requests even when a
    # browser attaches a session. This endpoint never persists a graph.
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        # Next's rewrite changes Host to the loopback API and OVERWRITES
        # X-Forwarded-Host with the browser's original Host (not its supplied
        # forwarding header). The API is loopback-only behind that boundary.
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if parsed.scheme not in {"http", "https"} or parsed.netloc != host:
            raise HTTPException(403, "Cross-origin graph requests are not allowed")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site graph requests are not allowed")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > _MAX_REQUEST:
            raise HTTPException(413, "Graph exceeds the request size limit")
        content.extend(chunk)
    try:
        if not isinstance(json.loads(content), dict):
            raise ValueError("Expected object")
    except (ValueError, RecursionError) as exc:
        raise HTTPException(422, "Graph request must be a JSON object") from exc
    return bytes(content)


async def _proxy(request: Request, path: str, client: httpx.AsyncClient) -> Response:
    await _authorize(request, client)
    entry = request.app.state.registry.by_id(_EQUIPMENT)
    if entry is None or not entry.enabled or entry.adapter != "http" or not entry.base_url:
        raise HTTPException(503, "Robot Motion is not configured or is disabled")
    if path in _DOC_PATHS:
        # Reuse the read-only docs renderer (Swagger execution is disabled).
        return RedirectResponse(f"/api/equipment/{_EQUIPMENT}/documentation/{path}",
                                status_code=303, headers={"Cache-Control": "no-store"})
    body = await _body(request) if request.method == "POST" else None
    headers = {"Accept-Encoding": "identity"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    outgoing = httpx.Request(
        request.method, f"{entry.base_url.rstrip('/')}/{path}",
        headers=headers, content=body,
        extensions={"timeout": httpx.Timeout(_TIMEOUT).as_dict()},
    )
    upstream = await client.send(outgoing, stream=True, auth=None, follow_redirects=False)
    try:
        if 300 <= upstream.status_code < 400:
            raise HTTPException(502, "Robot Motion returned an unexpected redirect")
        if upstream.headers.get("content-encoding", "identity").lower() != "identity":
            raise HTTPException(502, "Robot Motion returned unsupported encoding")
        media = upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        expected = _GET_TYPES.get(path, {"application/json"})
        if media not in ({"application/json", "text/plain"} if upstream.is_error else expected):
            raise HTTPException(502, "Robot Motion returned an unexpected content type")
        content = bytearray()
        async for chunk in upstream.aiter_bytes(chunk_size=64 * 1024):
            if len(content) + len(chunk) > _MAX_RESPONSE:
                raise HTTPException(502, "Robot Motion response exceeds the size limit")
            content.extend(chunk)
        try:
            decoded = content.decode("utf-8")
            if media == "application/json":
                json.loads(decoded)
        except (ValueError, RecursionError) as exc:
            raise HTTPException(502, "Robot Motion returned invalid text or JSON") from exc
        return Response(decoded, status_code=upstream.status_code, media_type=media, headers=_HEADERS)
    finally:
        await upstream.aclose()


def build_robot_motion_router() -> APIRouter:
    router = APIRouter(prefix=f"/api/robot-motion/{_EQUIPMENT}", tags=["robot-motion"])

    @router.get("/{panel_path:path}")
    @router.post("/{panel_path:path}")
    async def panel(panel_path: str, request: Request) -> Response:
        allowed = (_GET_TYPES.keys() | _DOC_PATHS) if request.method == "GET" else _POST_PATHS
        if panel_path not in allowed or request.url.query:
            raise HTTPException(404, "Robot Motion panel path is not available")
        # Separate from the control client's credential/cookie jar. An injected
        # client is only for offline tests. No pooled idle device connections.
        client = getattr(request.app.state, "robot_motion_client", None)
        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient(trust_env=False)
        try:
            return await asyncio.wait_for(_proxy(request, panel_path, client), _TIMEOUT)
        except HTTPException as exc:
            exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
            raise
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            raise HTTPException(504, "Robot Motion panel request timed out") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Robot Motion panel is unavailable") from exc
        finally:
            if owns_client:
                await client.aclose()

    return router
