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
- ``GET  /admin/accounts``            — admin-only roster view + last-login/session counts.
- ``GET  /admin/auth-events``         — admin-only sign-in audit log (auth_events).
- ``GET  /admin/sessions``            — admin-only live session list.
- ``GET  /admin/api-keys``            — admin-only key inventory (incl. last_used_at).
- ``GET  /admin/state``               — admin-only roster/reload/housekeeping state.

Allow-list management + the first admin: ``python -m ac_auth.cli`` (see cli.py).
Run: ``uvicorn ac_auth.main:app --host 127.0.0.1 --port 8009``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

_STATIC_DIR = Path(__file__).resolve().parent / "static"
from pydantic import BaseModel, Field

from .authz import (
    data_scope,
    effective_central_role,
    effective_device_role,
    path_permitted,
)
from .config import Settings, build_mailer, load_settings
from .db import Db, User, norm_email
from .platforms import load_membership
from .roster import Grant, Roster, RosterAutomation, RosterUser, load_roster, reload_roster
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
        grants=_automation_grants(a),
        name=a.name,
        expires_at=a.expires_at,
    )


def _automation_grants(a: RosterAutomation) -> list:
    """The account's declared scope, as grants ``authz`` can resolve.

    ``platform: hte`` is shorthand for one platform-scoped grant, so both it and
    an explicit ``grants:`` list reach :func:`authz.effective_device_role`
    through the same path. Empty means undeclared, which that resolver reads as
    lab-wide. The grant ``role`` is vestigial here (the device role is always
    ``automation``); only scope/id are read, and ``operator`` keeps the value
    inside what ``Grant`` accepts at equipment scope.
    """
    grants = list(a.grants)
    if a.platform:
        grants.append(Grant(scope="platform", id=a.platform, role="operator"))
    return grants


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


def _client_meta(request: Request) -> tuple[str, str]:
    """(ip, user_agent) for the audit log. Behind the Next middleware / Caddy
    the direct peer is the proxy, so prefer the first X-Forwarded-For hop; on
    the Tailnet that IP identifies the calling machine."""
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")
    return ip, request.headers.get("user-agent", "")


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
            # Housekeeping on the DB we own (production path — injected test DBs
            # are left alone): expired sessions / stale codes are safe to drop
            # now that auth_events is the durable login record.
            purged = app.state.db.purge_expired()
            if any(purged):
                logger.info("purged %d expired sessions, %d stale login codes", *purged)
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

        app.state.roster_loaded_at = time.time()
        # Last SIGHUP reload outcome, surfaced on /admin/state — keep-last-good
        # rejections are otherwise invisible outside the journal.
        app.state.last_roster_reload = None

        # SIGHUP → hot-reload the roster + membership, keeping the last-good copy
        # on any validation failure or mass-change breach (never drops to a broken
        # list).
        loop = asyncio.get_running_loop()

        def _reload_roster() -> None:
            result = reload_roster(None, app.state.roster)
            app.state.last_roster_reload = {
                "ts": time.time(),
                "applied": result.applied,
                "errors": result.errors,
            }
            if result.applied:
                app.state.roster = result.roster
                app.state.membership = load_membership()
                logger.info("roster reloaded (%d users)", len(result.roster.users))
                app.state.db.record_auth_event(
                    "roster_reload_applied",
                    detail=f"{len(result.roster.users)} users",
                )
            else:
                logger.error("roster reload REJECTED, keeping last-good: %s", "; ".join(result.errors))
                app.state.db.record_auth_event(
                    "roster_reload_rejected", detail="; ".join(result.errors)
                )

        try:
            # getattr, not signal.SIGHUP: on Windows the attribute itself does
            # not exist, so a direct reference raises AttributeError before
            # add_signal_handler's own guard can fire (found starting the
            # sidecar on a device PC, 2026-08-11).
            loop.add_signal_handler(getattr(signal, "SIGHUP"), _reload_roster)
        except (AttributeError, NotImplementedError, ValueError, RuntimeError):
            # no SIGHUP or no event-loop signal support here (Windows, or a
            # loop not on the main thread as under TestClient) —
            # reload-on-SIGHUP is best-effort; a full restart always picks up
            # roster changes.
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
        ip, ua = _client_meta(request)
        user = _lookup_principal(roster, email)
        # Clear 403 for an unknown email (internal tool, small allow-list). For a
        # fully public deployment, return a generic 202 here to avoid enumeration.
        if user is None or user.status != "active":
            await asyncio.to_thread(
                db.record_auth_event, "login_rejected", email,
                detail="not on the allow-list or inactive", ip=ip, user_agent=ua,
            )
            raise HTTPException(
                status_code=403,
                detail="This email is not authorized. Ask an admin to add you.",
            )
        if user.is_expired():
            await asyncio.to_thread(
                db.record_auth_event, "login_rejected", email,
                detail="account expired", ip=ip, user_agent=ua,
            )
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
        await asyncio.to_thread(
            db.record_auth_event, "code_requested", email, ip=ip, user_agent=ua
        )
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
        ip, ua = _client_meta(request)
        ok = await asyncio.to_thread(db.verify_login_code, email, body.code.strip(), s.code_max_attempts)
        if not ok:
            await asyncio.to_thread(
                db.record_auth_event, "login_failed", email,
                detail="invalid or expired code", ip=ip, user_agent=ua,
            )
            raise HTTPException(status_code=401, detail="Invalid or expired code.")
        user = _lookup_principal(roster, email)
        if user is None or user.status != "active" or user.is_expired():
            raise HTTPException(status_code=403, detail="This email is not authorized.")
        token = await asyncio.to_thread(db.create_session, email, s.session_ttl_s)
        await asyncio.to_thread(
            db.record_auth_event, "login_success", email, ip=ip, user_agent=ua
        )
        response.set_cookie(
            s.cookie_name, token, max_age=s.session_ttl_s, httponly=True,
            secure=s.cookie_secure, samesite="lax", path="/",
        )
        return {"ok": True, "email": email, "role": user.role}

    @app.get("/auth/verify")
    async def verify(request: Request) -> Response:
        """Forward-auth: 200 + identity headers when a session cookie (human) or
        ``X-Api-Key`` (machine principal) is valid, else 401. Caddy copies
        X-Auth-* downstream so control.py stamps the real owner into the claim.

        Unauthenticated **browser page navigations** (``Accept: text/html``) get a
        302 to the login page instead of a raw 401: behind the edge's
        ``forward_auth``, Caddy copies this 3xx (incl. ``Location``) back to the
        browser on the deny path, so a logged-out user landing on a gated path
        (e.g. ``/xarm5``) is sent to login rather than shown a bare 401. API / XHR
        callers (``fetch``, the Next middleware) send ``*/*`` or JSON and still get
        401, so their programmatic auth checks are unchanged."""
        user = await _session_user(request) or await _api_key_user(request)
        if user is None:
            accept = request.headers.get("accept", "").lower()
            if "text/html" in accept:
                login_url = os.environ.get("AUTH_LOGIN_URL", "/")
                return RedirectResponse(url=login_url, status_code=302)
            raise HTTPException(status_code=401, detail="not authenticated")
        roster = _roster(request)

        # Phase 2 — edge-path policy. Grants are service-level, so a machine
        # principal granted `analytica_db` would otherwise reach the experiment
        # design and analysis routes alongside the raw measurements it needs.
        # A principal with a `paths:` block in the roster is restricted to it;
        # everyone else is unaffected. See docs/HERMES_ACCESS_DESIGN.md.
        #
        # The URI comes from the edge (Caddy's forward_auth sends the ORIGINAL
        # request URI, before handle_path strips a prefix), so patterns are
        # written against edge paths like /analytica/measurements. If the header
        # is absent we cannot tell what is being authorized, so a path-scoped
        # principal is refused rather than waved through — failing open here
        # would make the whole policy a suggestion.
        policy = roster.path_policy(user.email)
        if policy is not None:
            forwarded_uri = request.headers.get("x-forwarded-uri")
            if forwarded_uri is None or not path_permitted(policy, forwarded_uri):
                raise HTTPException(
                    status_code=403,
                    detail="path not permitted for this principal",
                )

        # Also propagate the project-based data scope so forward_auth-fronted
        # services (lab.db reads, AnaliticaDB) can authorize without a second call.
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

    # ---- admin read endpoints (back the dashboard's /admin page) -----------

    async def _require_admin(request: Request) -> User:
        """401 without a valid principal, 403 unless it resolves to admin —
        the same gate as /authz/matrix (these enumerate the allow-list and
        the sign-in history)."""
        caller = await _session_user(request) or await _api_key_user(request)
        if caller is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        if not data_scope(caller, member_projects=(), pi_projects=()).is_admin:
            raise HTTPException(status_code=403, detail="admin only")
        return caller

    _MAX_EVENT_LIMIT = 500

    @app.get("/admin/accounts")
    async def admin_accounts(request: Request) -> dict:
        """Full roster view (including disabled/expired accounts, unlike
        /auth/users) decorated with last-login and live-session counts."""
        await _require_admin(request)
        db, roster = _db(request), _roster(request)

        def _collect() -> dict:
            humans = []
            for u in roster.users:
                humans.append(
                    {
                        "email": u.email,
                        "name": u.name,
                        "role": u.role,
                        "status": u.status,
                        "lab_account": u.lab_account,
                        "notes": u.notes,
                        "expires_at": u.expires_at,
                        "is_expired": u.is_expired,
                        "disabled_reason": u.disabled_reason,
                        "grants": [g.model_dump(exclude_none=True) for g in u.grants],
                        "last_login_at": db.last_login_at(u.email),
                        "active_sessions": db.count_active_sessions(u.email),
                    }
                )
            automation = [
                {
                    "email": a.email,
                    "name": a.name,
                    "approved": a.approved,
                    "platform": a.platform,
                    "expires_at": a.expires_at,
                    "is_expired": a.is_expired,
                    "notes": a.notes,
                    "api_keys": len([k for k in db.list_api_keys(a.email) if not k.revoked]),
                }
                for a in roster.automation
            ]
            return {"users": humans, "automation": automation}

        return await asyncio.to_thread(_collect)

    @app.get("/admin/auth-events")
    async def admin_auth_events(request: Request, limit: int = 100, email: str = "") -> dict:
        """Newest-first sign-in audit log (code requests, successes, failures,
        rejections, logouts, roster reloads)."""
        await _require_admin(request)
        db = _db(request)
        rows = await asyncio.to_thread(
            db.list_auth_events, email=email or None, limit=min(limit, _MAX_EVENT_LIMIT)
        )
        return {
            "events": [
                {
                    "ts": e.ts,
                    "email": e.email,
                    "event": e.event,
                    "detail": e.detail,
                    "ip": e.ip,
                    "user_agent": e.user_agent,
                }
                for e in rows
            ]
        }

    @app.get("/admin/sessions")
    async def admin_sessions(request: Request) -> dict:
        """Live (unexpired) sessions — who holds a signed-in browser right now —
        plus ``total_time_s``, the all-time signed-in seconds reconstructed
        from auth_events (see Db.total_session_time_s for the model)."""
        await _require_admin(request)
        db = _db(request)
        rows = await asyncio.to_thread(db.list_active_sessions)
        total_time_s = await asyncio.to_thread(
            db.total_session_time_s, request.app.state.settings.session_ttl_s
        )
        return {
            "sessions": [
                {"email": s.email, "created_at": s.created_at, "expires_at": s.expires_at}
                for s in rows
            ],
            "total_time_s": round(total_time_s, 1),
        }

    @app.get("/admin/api-keys")
    async def admin_api_keys(request: Request) -> dict:
        """Key inventory across all machine principals, incl. last_used_at so
        dead keys are distinguishable from load-bearing ones before revoking."""
        await _require_admin(request)
        keys = await asyncio.to_thread(_db(request).list_all_api_keys)
        return {
            "keys": [
                {
                    "id": k.id,
                    "email": k.email,
                    "label": k.label,
                    "created_at": k.created_at,
                    "expires_at": k.expires_at,
                    "revoked": k.revoked,
                    "last_used_at": k.last_used_at,
                }
                for k in keys
            ]
        }

    @app.get("/admin/state")
    async def admin_state(request: Request) -> dict:
        """Operational state for the admin page: roster shape, the last SIGHUP
        reload outcome (keep-last-good rejections are otherwise invisible
        outside the journal), pending automation approvals, and accounts
        expiring within 30 days."""
        await _require_admin(request)
        roster = _roster(request)
        now = time.time()
        soon = now + 30 * 86400
        expiring = [
            {"email": u.email, "expires_at": u.expires_at}
            for u in [*roster.users, *roster.automation]
            if u.expires_at is not None and now < u.expires_at < soon
        ]
        return {
            "roster": {
                "users": len(roster.users),
                "automation": len(roster.automation),
                "projects": len(roster.projects),
                "active_accounts": len(_active_principals(roster)),
            },
            "roster_loaded_at": getattr(request.app.state, "roster_loaded_at", None),
            "last_reload": getattr(request.app.state, "last_roster_reload", None),
            "pending_automation": [
                a.email for a in roster.automation if not a.approved
            ],
            "expiring_soon": sorted(expiring, key=lambda e: e["expires_at"]),
        }

    # ---- overview read endpoints (aggregate-only, any signed-in user) -------
    #
    # Back the Overview page's "Accounts & Activities" headline tile, which is
    # visible to every signed-in user (not just admins). They return ONLY
    # aggregate figures — roster counts and live-session summaries — never the
    # account/session listings the /admin/* endpoints expose. Access requires a
    # valid session of any role (401 otherwise); the full /admin/* detail stays
    # admin-only in _require_admin above.

    async def _require_authenticated(request: Request) -> User:
        """401 without a valid session principal — any role may read the
        aggregate overview figures, since they reveal no per-account detail."""
        caller = await _session_user(request)
        if caller is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        return caller

    @app.get("/overview/state")
    async def overview_state(request: Request) -> dict:
        """Headline roster counts for the Overview tile — numbers only, no
        account listing (that lives behind /admin/state)."""
        await _require_authenticated(request)
        roster = _roster(request)
        return {
            "roster": {
                "users": len(roster.users),
                "automation": len(roster.automation),
                "projects": len(roster.projects),
                "active_accounts": len(_active_principals(roster)),
            },
        }

    @app.get("/overview/sessions")
    async def overview_sessions(request: Request) -> dict:
        """Aggregate live-session figures plus all-time signed-in seconds for
        the Overview tile — counts/summaries only (no emails). ``seconds`` is
        Σ(now − created_at) across live sessions, computed against a single
        ``now`` for a self-consistent snapshot."""
        await _require_authenticated(request)
        db = _db(request)
        now = time.time()
        rows = await asyncio.to_thread(db.list_active_sessions)
        seconds = sum(max(0.0, now - s.created_at) for s in rows)
        accounts = len({s.email for s in rows})
        total_time_s = await asyncio.to_thread(
            db.total_session_time_s, request.app.state.settings.session_ttl_s
        )
        return {
            "live": {
                "count": len(rows),
                "accounts": accounts,
                "seconds": round(seconds, 1),
            },
            "total_time_s": round(total_time_s, 1),
        }

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
            # Resolve the email before revoking so the audit row names the account.
            email = await asyncio.to_thread(db.session_email, token)
            await asyncio.to_thread(db.revoke_session, token)
            if email:
                ip, ua = _client_meta(request)
                await asyncio.to_thread(
                    db.record_auth_event, "logout", email, ip=ip, user_agent=ua
                )
        response.delete_cookie(s.cookie_name, path="/")
        return {"ok": True}

    @app.get("/auth/banner.js")
    async def banner_js() -> Response:
        """Self-contained shared top banner. Every lab UI behind the single edge
        opts in with one line — ``<script src="/auth/banner.js" defer></script>``
        — and gets the same login/logout bar, driven by this one asset. It calls
        only same-origin /auth/* endpoints, so the host-only session cookie rides
        along; markup/styling/logic live here, so updating it updates every UI.
        Served ungated (it is the login surface). No-store so a banner update is
        picked up on the next page load rather than lingering in caches."""
        return FileResponse(
            _STATIC_DIR / "banner.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    return app


app = create_app()
