"""Auth sidecar FastAPI app (Phase 1 — audit mode).

Endpoints:
- ``GET /health``      — liveness; reports whether enforcement is on.
- ``GET /auth/verify`` — the forward-auth endpoint. Resolves the caller's
  Tailscale identity and **logs** it. In audit mode (default) it ALWAYS
  returns 200 so nothing is gated yet; it sets ``X-Auth-*`` response headers
  that a later phase's Caddy ``forward_auth`` can copy downstream.
- ``GET /auth/me``     — identity for the frontend lock chip.

Run: ``uvicorn ac_auth.main:app --host 127.0.0.1 --port 8009``

Enforcement is gated behind ``AUTH_ENFORCE`` (default off). Phase 1 keeps it
off everywhere; flipping it on is Phase 2 and also requires the Caddy
``forward_auth`` wiring (see ``docs/AUTH.md`` and
``deploy/Caddyfile.auth-snippet``).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .identity import Identity, TailscaleIdentityResolver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ac_auth")


def _enforce_enabled() -> bool:
    return os.environ.get("AUTH_ENFORCE", "false").strip().lower() in ("1", "true", "yes", "on")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tests may pre-seed app.state.resolver with a fake; don't clobber it.
    if not getattr(app.state, "resolver", None):
        app.state.resolver = TailscaleIdentityResolver()
    logger.info("auth sidecar up (enforce=%s)", _enforce_enabled())
    try:
        yield
    finally:
        await app.state.resolver.aclose()


app = FastAPI(title="AC Organic Lab — Auth sidecar", version="0.1.0", lifespan=lifespan)


def _client_ip(request: Request, x_forwarded_for: Optional[str]) -> str:
    """The caller's Tailnet source IP.

    Behind Caddy ``forward_auth`` the original client IP arrives as the first
    hop of ``X-Forwarded-For``; when the sidecar is hit directly we fall back
    to the socket peer. (Phase 2 should pin a trusted-proxy allowlist before
    enforcement leans on XFF.)
    """
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else ""


async def _resolve(request: Request, xff: Optional[str]) -> tuple[str, Optional[Identity]]:
    ip = _client_ip(request, xff)
    ident = await request.app.state.resolver.whois(ip) if ip else None
    return ip, ident


def _identity_payload(ident: Optional[Identity]) -> Optional[dict]:
    if ident is None:
        return None
    return {
        "login": ident.login,
        "display": ident.display,
        "node": ident.node,
        "tags": list(ident.tags),
        "tagged": ident.is_tagged,
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "enforce": _enforce_enabled()}


@app.get("/auth/verify")
async def verify(
    request: Request,
    x_forwarded_for: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Forward-auth endpoint. Audit mode: log identity, always allow."""
    ip, ident = await _resolve(request, x_forwarded_for)
    enforce = _enforce_enabled()

    if ident is None:
        logger.info("verify addr=%s -> NO IDENTITY (enforce=%s)", ip, enforce)
        if enforce:
            raise HTTPException(status_code=401, detail="no Tailscale identity for caller")
        return _allow(None)

    who = f"TAGGED[{','.join(ident.tags)}]" if ident.is_tagged else ident.login
    logger.info("verify addr=%s -> %s (tagged=%s, enforce=%s)", ip, who, ident.is_tagged, enforce)
    if enforce and ident.is_tagged:
        # A tagged infra node is not a human operator; Phase 2 policy decision.
        raise HTTPException(status_code=403, detail="caller is a tagged node, not a user")
    return _allow(ident)


def _allow(ident: Optional[Identity]) -> JSONResponse:
    headers: dict[str, str] = {}
    if ident is not None:
        # Phase 2's Caddy forward_auth `copy_headers` will forward these to the
        # dashboard so control.py can stamp the real user into claims/audit.
        headers["X-Auth-User"] = ident.login
        headers["X-Auth-Display"] = ident.display
        headers["X-Auth-Tagged"] = "1" if ident.is_tagged else "0"
    return JSONResponse({"ok": True, "identity": _identity_payload(ident)}, headers=headers)


@app.get("/auth/me")
async def me(
    request: Request,
    x_forwarded_for: Optional[str] = Header(default=None),
) -> dict:
    _ip, ident = await _resolve(request, x_forwarded_for)
    if ident is None:
        return {"authenticated": False, "identity": None}
    return {"authenticated": True, "identity": _identity_payload(ident)}
