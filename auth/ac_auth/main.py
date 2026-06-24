"""Auth sidecar FastAPI app — passwordless email one-time-code login.

A human enters their email; if it's on the allow-list (``users`` table) we email
a single-use code (via Gmail, see :mod:`ac_auth.smtp_mailer`); they submit the
code and get an opaque **session cookie**. Caddy ``forward_auth`` then calls
``GET /auth/verify`` on every protected request to validate that cookie and
inject ``X-Auth-User`` / ``X-Auth-Role`` downstream.

Endpoints:
- ``GET  /health``            — liveness.
- ``POST /auth/request-code`` — ``{email}`` → email a code (403 if not allow-listed).
- ``POST /auth/verify-code``  — ``{email, code}`` → set session cookie.
- ``GET  /auth/verify``       — forward-auth: validate cookie → 200 + headers / 401.
- ``GET  /auth/me``           — identity for the frontend.
- ``POST /auth/logout``       — revoke session + clear cookie.

Allow-list management + the first admin: ``python -m ac_auth.cli`` (see cli.py).
Run: ``uvicorn ac_auth.main:app --host 127.0.0.1 --port 8009``.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Settings, build_mailer, load_settings
from .db import Db
from .smtp_mailer import MailSendError, new_code

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ac_auth")


class EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class VerifyIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=4, max_length=12)


def create_app(
    *,
    settings: Optional[Settings] = None,
    db: Optional[Db] = None,
    mailer=None,
) -> FastAPI:
    """Build the app. Tests inject ``settings``/``db``/``mailer``; in production
    the lifespan creates the real ones."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "settings", None) is None:
            app.state.settings = load_settings()
        if getattr(app.state, "db", None) is None:
            app.state.db = Db(app.state.settings.db_path)
        if getattr(app.state, "mailer", None) is None:
            app.state.mailer = build_mailer()
        logger.info("auth sidecar up (db=%s)", app.state.settings.db_path)
        try:
            yield
        finally:
            if getattr(app.state, "db", None) is not None:
                app.state.db.close()
            m = getattr(app.state, "mailer", None)
            if m is not None and hasattr(m, "aclose"):
                await m.aclose()

    app = FastAPI(title="AC Organic Lab - Auth sidecar", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.mailer = mailer

    def _s(request: Request) -> Settings:
        return request.app.state.settings

    def _db(request: Request) -> Db:
        return request.app.state.db

    async def _session_user(request: Request):
        s, db = _s(request), _db(request)
        token = request.cookies.get(s.cookie_name)
        email = await asyncio.to_thread(db.session_email, token) if token else None
        if not email:
            return None
        user = await asyncio.to_thread(db.get_user, email)
        return user if (user and user.status == "active") else None

    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy"}

    @app.post("/auth/request-code", status_code=202)
    async def request_code(body: EmailIn, request: Request) -> dict:
        s, db = _s(request), _db(request)
        email = body.email.strip().lower()
        if "@" not in email:
            raise HTTPException(status_code=422, detail="invalid email")
        user = await asyncio.to_thread(db.get_user, email)
        # Clear 403 for an unknown email (internal tool, small allow-list). For a
        # fully public deployment, return a generic 202 here to avoid enumeration.
        if user is None or user.status != "active":
            raise HTTPException(
                status_code=403,
                detail="This email is not authorized. Ask an admin to add you.",
            )
        code = new_code()
        await asyncio.to_thread(db.create_login_code, email, code, s.code_ttl_s)
        try:
            await request.app.state.mailer.send_login_code(
                email, code, ttl_minutes=max(1, s.code_ttl_s // 60)
            )
        except MailSendError as exc:
            logger.error("send_login_code failed for %s: %s", email, exc)
            raise HTTPException(status_code=502, detail="Could not send the code email; try again.")
        return {"sent": True, "message": f"A sign-in code was emailed to {email}."}

    @app.post("/auth/verify-code")
    async def verify_code(body: VerifyIn, request: Request, response: Response) -> dict:
        s, db = _s(request), _db(request)
        email = body.email.strip().lower()
        ok = await asyncio.to_thread(db.verify_login_code, email, body.code.strip(), s.code_max_attempts)
        if not ok:
            raise HTTPException(status_code=401, detail="Invalid or expired code.")
        user = await asyncio.to_thread(db.get_user, email)
        if user is None or user.status != "active":
            raise HTTPException(status_code=403, detail="This email is not authorized.")
        token = await asyncio.to_thread(db.create_session, email, s.session_ttl_s)
        response.set_cookie(
            s.cookie_name, token, max_age=s.session_ttl_s, httponly=True,
            secure=s.cookie_secure, samesite="lax", path="/",
        )
        return {"ok": True, "email": email, "role": user.role}

    @app.get("/auth/verify")
    async def verify(request: Request) -> JSONResponse:
        """Forward-auth: 200 + identity headers when the session cookie is valid,
        else 401. Caddy copies X-Auth-* downstream so control.py stamps the user."""
        user = await _session_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        return JSONResponse(
            {"ok": True, "email": user.email, "role": user.role},
            headers={"X-Auth-User": user.email, "X-Auth-Role": user.role},
        )

    @app.get("/auth/me")
    async def me(request: Request) -> dict:
        user = await _session_user(request)
        if user is None:
            return {"authenticated": False, "identity": None}
        return {"authenticated": True, "identity": {"email": user.email, "role": user.role}}

    @app.post("/auth/logout")
    async def logout(request: Request, response: Response) -> dict:
        s, db = _s(request), _db(request)
        token = request.cookies.get(s.cookie_name)
        if token:
            await asyncio.to_thread(db.revoke_session, token)
        response.delete_cookie(s.cookie_name, path="/")
        return {"ok": True}

    return app


app = create_app()
