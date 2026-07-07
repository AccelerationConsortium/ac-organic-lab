"""Auth sidecar FastAPI app — passwordless email one-time-code login.

A human enters their email; if it's on the allow-list (``users`` table) we email
a single-use code (via Gmail, see :mod:`ac_auth.smtp_mailer`); they submit the
code and get an opaque **session cookie**. Caddy ``forward_auth`` then calls
``GET /auth/verify`` on every protected request to validate that cookie and
inject ``X-Auth-User`` / ``X-Auth-Role`` downstream.

Endpoints:
- ``GET  /health``                    — liveness.
- ``POST /auth/login``                — ``{id|email}`` → email a code (403 if not allow-listed).
- ``POST /auth/verify-code``          — ``{id|email, code}`` → set session cookie.
- ``GET  /auth/verify``               — forward-auth: validate cookie **or** ``X-Api-Key``
  (machine principals) → 200 + X-Auth-* headers / 401.
- ``GET  /auth/users``                — ``{id, name, role}`` for the login dropdown (no email).
- ``GET  /auth/me``                   — identity for the frontend.
- ``POST /auth/logout``               — revoke session + clear cookie.
- ``GET  /equipment/{key}/roster``    — owner→device-role projection a device pulls
  (device-plane, Tailnet-only).
- ``GET  /authz/check``               — effective-role probe for one (user, equipment).
- ``GET  /authz/scope``               — project-based data scope for a principal.
- ``GET  /authz/mine``                — the caller's own equipment→role map (for the UI).
- ``GET  /authz/matrix``              — admin-only users × equipment → role matrix.

Allow-list management + the first admin: ``python -m ac_auth.cli`` (see cli.py).
Run: ``uvicorn ac_auth.main:app --host 127.0.0.1 --port 8009``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import signal
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .authz import data_scope, effective_central_role, effective_device_role
from .config import Settings, build_mailer, load_settings
from .db import Db, User, norm_email
from .platforms import load_membership
from .roster import Roster, RosterAutomation, RosterUser, load_roster, reload_roster
from .smtp_mailer import MailSendError, new_code

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ac_auth")


class EmailIn(BaseModel):
    # Login accepts either an opaque dropdown ``id`` (so the client never holds a
    # raw email) or a raw ``email`` (CLI / back-compat). At least one is required.
    email: Optional[str] = Field(default=None, max_length=254)
    id: Optional[str] = Field(default=None, max_length=64)


class VerifyIn(BaseModel):
    email: Optional[str] = Field(default=None, max_length=254)
    id: Optional[str] = Field(default=None, max_length=64)
    code: str = Field(min_length=4, max_length=12)


# ---------------------------------------------------------------------------
# Identity resolution from the roster (Phase 0)
#
# The allow-list now lives in roster.yaml; SQLite holds only sessions / codes /
# keys. These map a roster entry to the in-memory :class:`User` principal the
# routes + authz already understand. The roster role ``operator`` maps to the
# wire/device value ``user`` (operator == user); ``admin`` is unchanged.
# ---------------------------------------------------------------------------


def _human_user(u: RosterUser) -> User:
    # preserve "none" (no global access) and "admin"; everything else is operator
    # (the wire/legacy value "user"). authz resolves the effective role from this
    # plus grants.
    flat = "admin" if u.role == "admin" else ("none" if u.role == "none" else "user")
    return User(
        email=u.email,
        role=flat,
        status=u.status,
        is_automation=False,
        grants=list(u.grants),
        name=u.name,
        lab_account=u.lab_account,
        notes=u.notes,
        expires_at=u.expires_at,
    )


def _automation_user(a: RosterAutomation) -> User:
    # an un-approved automation account is treated as disabled (its keys never authenticate)
    return User(
        email=a.email,
        role="user",
        status="active" if a.approved else "disabled",
        is_automation=True,
        name=a.name,
        expires_at=a.expires_at,
    )


def _login_id(email: str) -> str:
    """Opaque, stable, non-reversible handle used as the login-dropdown option
    value so the client never receives a raw email address (privacy on a public
    login page). Derived from the email; reversed server-side by scanning the
    roster (see :func:`_email_from_login_id`)."""
    return hashlib.sha256(("login:" + norm_email(email)).encode("utf-8")).hexdigest()[:16]


def _email_from_login_id(roster: Roster, login_id: Optional[str]) -> Optional[str]:
    """Reverse a dropdown ``id`` back to its email (human accounts only)."""
    if not login_id:
        return None
    for u in roster.users:
        if _login_id(u.email) == login_id:
            return u.email
    return None


def _lookup_principal(roster: Roster, email: Optional[str]) -> Optional[User]:
    """Resolve an email to its principal from the roster, or None if not listed."""
    if not email:
        return None
    email = norm_email(email)
    for u in roster.users:
        if u.email == email:
            return _human_user(u)
    for a in roster.automation:
        if a.email == email:
            return _automation_user(a)
    return None


def _active_principals(roster: Roster) -> list[User]:
    """Every account currently allowed to authenticate (active, non-expired)."""
    out = [_human_user(u) for u in roster.users]
    out += [_automation_user(a) for a in roster.automation]
    return [p for p in out if p.status == "active" and not p.is_expired()]


def create_app(
    *,
    settings: Optional[Settings] = None,
    db: Optional[Db] = None,
    mailer=None,
    roster: Optional[Roster] = None,
    membership: Optional[dict] = None,
) -> FastAPI:
    """Build the app. Tests inject ``settings``/``db``/``mailer``/``roster``/
    ``membership``; in production the lifespan creates the real ones (and loads
    roster.yaml + platforms.yaml)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "settings", None) is None:
            app.state.settings = load_settings()
        if getattr(app.state, "db", None) is None:
            app.state.db = Db(app.state.settings.db_path)
        if getattr(app.state, "mailer", None) is None:
            app.state.mailer = build_mailer()
        if getattr(app.state, "roster", None) is None:
            # Fail closed: a missing/invalid roster aborts startup rather than
            # coming up with an empty (or worse, permissive) allow-list.
            app.state.roster = load_roster()
        if getattr(app.state, "membership", None) is None:
            # platform↔equipment membership (fail-soft → {} → platform grants
            # simply don't resolve; global/equipment grants still do).
            app.state.membership = load_membership()

        # SIGHUP → hot-reload the roster + membership, keeping the last-good copy
        # on any validation failure or mass-change breach (never drops to a broken
        # list).
        loop = asyncio.get_running_loop()

        def _reload_roster() -> None:
            result = reload_roster(None, app.state.roster)
            if result.applied:
                app.state.roster = result.roster
                app.state.membership = load_membership()
                logger.info("roster reloaded (%d users)", len(result.roster.users))
            else:
                logger.error("roster reload REJECTED, keeping last-good: %s", "; ".join(result.errors))

        try:
            loop.add_signal_handler(signal.SIGHUP, _reload_roster)
        except (NotImplementedError, ValueError, RuntimeError):
            # no event-loop signal support here (Windows, or a loop not on the
            # main thread as under TestClient) — reload-on-SIGHUP is best-effort;
            # a full restart always picks up roster changes.
            _signal_registered = False
        else:
            _signal_registered = True

        logger.info(
            "auth sidecar up (db=%s, roster=%d users)",
            app.state.settings.db_path,
            len(app.state.roster.users),
        )
        try:
            yield
        finally:
            if _signal_registered:
                try:
                    loop.remove_signal_handler(signal.SIGHUP)
                except (NotImplementedError, ValueError, RuntimeError):
                    pass
            if getattr(app.state, "db", None) is not None:
                app.state.db.close()
            m = getattr(app.state, "mailer", None)
            if m is not None and hasattr(m, "aclose"):
                await m.aclose()

    app = FastAPI(title="AC Organic Lab - Auth sidecar", version="0.3.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.mailer = mailer
    app.state.roster = roster
    app.state.membership = membership

    def _s(request: Request) -> Settings:
        return request.app.state.settings

    def _db(request: Request) -> Db:
        return request.app.state.db

    def _roster(request: Request) -> Roster:
        return request.app.state.roster

    def _membership(request: Request) -> dict:
        return request.app.state.membership or {}

    async def _session_user(request: Request):
        s, db = _s(request), _db(request)
        token = request.cookies.get(s.cookie_name)
        email = await asyncio.to_thread(db.session_email, token) if token else None
        user = _lookup_principal(_roster(request), email)
        return user if (user and user.status == "active" and not user.is_expired()) else None

    async def _api_key_user(request: Request):
        """Machine principal authenticated by ``X-Api-Key`` (automation accounts —
        robot/platform). The api_keys table only proves possession of a live key;
        the principal's identity + approval is resolved from the roster."""
        db = _db(request)
        key = request.headers.get("x-api-key")
        if not key:
            return None
        email = await asyncio.to_thread(db.verify_api_key, key)
        user = _lookup_principal(_roster(request), email)
        return user if (user and user.status == "active" and not user.is_expired()) else None

    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy"}

    @app.get("/status")
    async def equipment_status(request: Request) -> dict:
        """STATUS_SPEC v1.0 envelope so the auth sidecar can appear as a tile
        under the dashboard's "Web Services" section. Side-effect-free: a single
        read of the allow-list for the active-user count."""
        try:
            n_users = len(_active_principals(_roster(request)))
        except Exception:
            n_users = 0
        return {
            "protocol_version": "1.0",
            "equipment_id": "ac_organic_lab_auth",
            "equipment_name": "Auth Sidecar",
            "equipment_kind": "other",
            "equipment_version": request.app.version,
            "host": socket.gethostname(),
            "equipment_status": "ready",
            "device_time": datetime.now(timezone.utc).isoformat(),
            "metrics": {
                "active_users": {"value": n_users, "unit": "users"},
            },
            "details": {},
        }

    @app.post("/auth/login", status_code=202)
    async def login(body: EmailIn, request: Request) -> dict:
        s, db = _s(request), _db(request)
        roster = _roster(request)
        email = (body.email or "").strip().lower()
        if not email and body.id:
            email = _email_from_login_id(roster, body.id.strip()) or ""
        if "@" not in email:
            raise HTTPException(status_code=422, detail="invalid account")
        user = _lookup_principal(roster, email)
        # Clear 403 for an unknown email (internal tool, small allow-list). For a
        # fully public deployment, return a generic 202 here to avoid enumeration.
        if user is None or user.status != "active":
            raise HTTPException(
                status_code=403,
                detail="This email is not authorized. Ask an admin to add you.",
            )
        if user.is_expired():
            raise HTTPException(
                status_code=403,
                detail="This account has expired. Ask an admin to extend it.",
            )
        # Anti-spam: throttle code emails per address so nobody can flood a real
        # user's inbox via /auth/login. Two limits — a short cooldown
        # between sends and a rolling-hour cap — both keyed on the target email,
        # computed from the login_codes send history. 429 + Retry-After.
        _WINDOW_S = 3600.0
        count, oldest, latest = await asyncio.to_thread(db.login_code_rate, email, _WINDOW_S)
        now = time.time()
        if latest is not None and (now - latest) < s.code_resend_cooldown_s:
            retry = int(s.code_resend_cooldown_s - (now - latest)) + 1
            raise HTTPException(
                status_code=429,
                detail=f"A sign-in code was just sent. Try again in {retry}s.",
                headers={"Retry-After": str(retry)},
            )
        if count >= s.code_max_per_hour:
            retry = int(_WINDOW_S - (now - oldest)) + 1 if oldest else int(_WINDOW_S)
            raise HTTPException(
                status_code=429,
                detail="Too many sign-in codes requested for this address. Try again later.",
                headers={"Retry-After": str(retry)},
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
        roster = _roster(request)
        email = (body.email or "").strip().lower()
        if not email and body.id:
            email = _email_from_login_id(roster, body.id.strip()) or ""
        if "@" not in email:
            raise HTTPException(status_code=422, detail="invalid account")
        ok = await asyncio.to_thread(db.verify_login_code, email, body.code.strip(), s.code_max_attempts)
        if not ok:
            raise HTTPException(status_code=401, detail="Invalid or expired code.")
        user = _lookup_principal(roster, email)
        if user is None or user.status != "active" or user.is_expired():
            raise HTTPException(status_code=403, detail="This email is not authorized.")
        # The session row IS the login record — last-login derives from it; no
        # separate touch_login write (the users table is retired in Phase 0).
        token = await asyncio.to_thread(db.create_session, email, s.session_ttl_s)
        response.set_cookie(
            s.cookie_name, token, max_age=s.session_ttl_s, httponly=True,
            secure=s.cookie_secure, samesite="lax", path="/",
        )
        return {"ok": True, "email": email, "role": user.role}

    @app.get("/auth/verify")
    async def verify(request: Request) -> JSONResponse:
        """Forward-auth: 200 + identity headers when a session cookie (human) or
        ``X-Api-Key`` (machine principal) is valid, else 401. Caddy copies
        X-Auth-* downstream so control.py stamps the real owner into the claim."""
        user = await _session_user(request) or await _api_key_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        # Also propagate the project-based data scope so forward_auth-fronted
        # services (lab.db reads, AnaliticaDB) can authorize without a second call.
        roster = _roster(request)
        scope = data_scope(
            user,
            member_projects=roster.member_projects(user.email),
            pi_projects=roster.pi_projects(user.email),
        )
        return JSONResponse(
            {"ok": True, "email": user.email, "role": user.role},
            headers={
                "X-Auth-User": user.email,
                "X-Auth-Role": user.role,
                "X-Auth-Projects": ",".join(sorted(scope.member_projects)),
                "X-Auth-Pi-Projects": ",".join(sorted(scope.pi_projects)),
            },
        )

    @app.get("/equipment/{equipment_key}/roster")
    async def equipment_roster(equipment_key: str, request: Request) -> dict:
        """Owner→device-role projection a device pulls to populate its local
        roster (defense-in-depth; stays valid if central is briefly unreachable).

        **Device-plane endpoint — Tailnet-only by deployment** (the device
        sidecars sit behind the Tailscale ACL; this is not exposed at the public
        Caddy edge). Returns every active account mapped through the single
        :func:`effective_device_role` seam, so the projection always agrees with
        what the platform would authorize. Scope-filtered since Phase 1b:
        accounts whose effective role on this equipment resolves to ``None``
        (e.g. ``role: none`` with no applicable grant) are excluded."""
        membership = _membership(request)
        entries = []
        for u in _active_principals(_roster(request)):
            role = effective_device_role(u, equipment_key, membership)
            if role is not None:  # exclude accounts with no access to this device
                entries.append({"owner": u.email, "role": role})
        return {"equipment_key": equipment_key, "entries": entries}

    @app.get("/authz/check")
    async def authz_check(equipment: str, request: Request, user: str = "") -> dict:
        """Authorization probe (Phase 2): the effective device role a principal
        holds on an equipment, resolving per-scope grants. Device-plane /
        peer-platform endpoint (Tailnet-only), same single resolver as the roster
        projection. ``user`` defaults to the authenticated caller when omitted."""
        email = user or ""
        if not email:
            caller = await _session_user(request) or await _api_key_user(request)
            if caller is None:
                raise HTTPException(status_code=401, detail="not authenticated")
            email = caller.email
        principal = _lookup_principal(_roster(request), email)
        active = bool(principal and principal.status == "active" and not principal.is_expired())
        if not active:
            return {
                "user": norm_email(email),
                "equipment": equipment,
                "allowed": False,
                "role": None,
                "reason": "not on the allow-list or inactive/expired",
            }
        membership = _membership(request)
        role = effective_device_role(principal, equipment, membership)
        if role is None:
            return {
                "user": principal.email,
                "equipment": equipment,
                "allowed": False,
                "role": None,
                "reason": "no grant for this equipment",
            }
        return {
            "user": principal.email,
            "equipment": equipment,
            "allowed": True,
            "role": role,
            "central_role": "automation"
            if principal.is_automation
            else effective_central_role(principal, equipment, membership),
        }

    @app.get("/authz/scope")
    async def authz_scope(request: Request, user: str = "") -> dict:
        """Project-based data scope (member_projects / pi_projects / is_admin) for
        a principal — consumed by the data plane's ``can_read`` (lab.db reads,
        AnaliticaDB catalog). Tailnet-only, same roster source as the role
        resolver. ``user`` defaults to the authenticated caller. An unknown or
        inactive principal → empty scope (no access), never an error."""
        email = user or ""
        if not email:
            caller = await _session_user(request) or await _api_key_user(request)
            if caller is None:
                raise HTTPException(status_code=401, detail="not authenticated")
            email = caller.email
        roster = _roster(request)
        principal = _lookup_principal(roster, email)
        empty = {"user": norm_email(email), "member_projects": [], "pi_projects": [], "is_admin": False}
        if principal is None:
            return empty
        # An inactive/expired account has no data access (member or owner).
        if principal.status != "active" or principal.is_expired():
            return {**empty, "user": principal.email}
        scope = data_scope(
            principal,
            member_projects=roster.member_projects(principal.email),
            pi_projects=roster.pi_projects(principal.email),
        )
        return {
            "user": principal.email,
            "member_projects": sorted(scope.member_projects),
            "pi_projects": sorted(scope.pi_projects),
            "is_admin": scope.is_admin,
        }

    @app.get("/authz/mine")
    async def authz_mine(request: Request) -> dict:
        """The authenticated caller's own equipment→role projection (Phase 2
        UI support): the dashboard fetches this after login to disable the
        control surfaces the user holds no role on. Same single resolver as
        ``/authz/check``. The equipment universe is `platforms.yaml`
        membership plus any equipment the caller reaches via an
        equipment-scoped grant (which resolves regardless of membership), so
        a restricted account still sees its granted devices listed."""
        caller = await _session_user(request) or await _api_key_user(request)
        if caller is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        membership = _membership(request)
        keys = set(membership) | {
            g.id
            for g in (getattr(caller, "grants", ()) or ())
            if getattr(g, "scope", None) == "equipment" and getattr(g, "id", None)
        }
        return {
            "user": caller.email,
            "role": caller.role,
            "equipment": {
                key: effective_device_role(caller, key, membership)
                for key in sorted(keys)
            },
        }

    @app.get("/authz/matrix")
    async def authz_matrix(request: Request) -> dict:
        """Access matrix (Phase 2): every active principal × every known
        equipment → effective device role, through the same single resolver as
        the roster projection and ``/authz/check`` — the human-readable "clear
        definition of who may do what" (requirement 1). **Admin-only**: it
        enumerates the entire allow-list. Equipment columns come from
        `platforms.yaml` membership — the same universe platform grants resolve
        against; equipment absent from any platform section is still reachable
        via ``/authz/check``, it just has no column here."""
        caller = await _session_user(request) or await _api_key_user(request)
        if caller is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        if not data_scope(caller, member_projects=(), pi_projects=()).is_admin:
            raise HTTPException(status_code=403, detail="admin only")
        membership = _membership(request)
        equipment = sorted(membership.keys())
        rows = []
        for u in _active_principals(_roster(request)):
            rows.append(
                {
                    "email": u.email,
                    "kind": "automation" if u.is_automation else "human",
                    "role": u.role,
                    "roles": {key: effective_device_role(u, key, membership) for key in equipment},
                }
            )
        return {"equipment": equipment, "users": rows}

    @app.get("/auth/users")
    async def users(request: Request) -> dict:
        """Active human accounts for the dashboard's login dropdown, as
        ``{id, name, role}`` — **no email**. ``id`` is an opaque, stable handle
        (see :func:`_login_id`) the client passes back to /auth/login and
        /auth/verify-code, so raw addresses never reach the browser (privacy on
        a public login page). ``name`` is the roster display name, falling back
        to a masked address only if a user has none. Automation accounts
        (API-key principals) are excluded. Sorted by name."""

        def _label(u: User) -> str:
            if u.name and u.name.strip():
                return u.name.strip()
            local, _, dom = u.email.partition("@")
            return f"{local[:1]}…@{dom}" if dom else "account"

        entries = [
            {"id": _login_id(u.email), "name": _label(u), "role": u.role}
            for u in _active_principals(_roster(request))
            if not u.is_automation
        ]
        entries.sort(key=lambda e: e["name"].lower())
        return {"users": entries}

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
