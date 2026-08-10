"""`roster.yaml` — the authoritative allow-list (Phase 0).

Config (who exists, their role/profile, and later their grants) is **declared
YAML**, version-controlled and git-reviewed; SQLite holds only runtime state
(sessions / login codes / API keys). This module parses + validates the roster
into typed models and exposes:

* :func:`load_roster` — parse + validate, **raise** :class:`RosterError` on any
  problem. Callers use this at **startup** so a broken roster *fails closed*
  (the service refuses to boot rather than come up with an empty/allow-all list).
* :func:`reload_roster` — for a **live reload**: on any validation failure *or* a
  mass-change breach it returns the *current* roster unchanged (**keep-last-good**)
  so a typo can't take auth down or lock everyone out.
* :func:`dump_roster` — render a :class:`Roster` back to YAML (used by the CLI
  ``export`` to capture the live DB state into a roster file).

Validation is layered: pydantic schema (shape/types) **plus** invariant guards
(>=1 active admin, unique emails, valid grants). See ``docs/AUTH_DESIGN.md``
"Storage model -> Safety".

NOTE (Phase 0): nothing wires this into the running service yet — the read path
still uses the SQLite ``users`` table. This module + the CLI ``validate`` /
``export`` land first so the generated ``roster.yaml`` can be reviewed before the
read-path swap (Phase 0 step 4).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Iterable, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

Role = Literal["operator", "admin"]  # grant roles
# A user's flat global role. "none" = no global access at all — the account can
# sign in but only reaches equipment via explicit grants (per-equipment
# restriction; Phase 1b). Default "operator" keeps existing entries unchanged.
UserRole = Literal["none", "operator", "admin"]
GrantScope = Literal["global", "platform", "equipment"]

# Default cap on how many active accounts a single reload may remove/disable
# before it is rejected as a likely accident (override AUTH_ROSTER_MAX_REVOCATIONS,
# or pass force=True). Startup load is never subject to this — only live reloads.
_DEFAULT_MAX_REVOCATIONS = 3


class RosterError(ValueError):
    """Roster failed schema or invariant validation. ``errors`` is a list of
    human-readable messages for the CLI / logs."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _norm_email(value: str) -> str:
    return value.strip().lower()


def _validate_email(value: str) -> str:
    value = _norm_email(value)
    if "@" not in value or value.startswith("@") or value.endswith("@"):
        raise ValueError(f"invalid email: {value!r}")
    return value


def _end_of_day_epoch(d: date) -> float:
    """An expiry *date* → epoch seconds at 23:59:59 UTC of that day, so an account
    stays valid through the whole named day (matches the CLI's behaviour)."""
    return datetime.combine(d, time(23, 59, 59), tzinfo=timezone.utc).timestamp()


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


class Grant(BaseModel):
    """A per-scope authorization (Phase 1), consulted by
    :func:`ac_auth.authz.effective_central_role` (humans) and
    :func:`ac_auth.authz.effective_device_role` (automation scope)."""

    model_config = ConfigDict(extra="forbid")

    scope: GrantScope
    id: Optional[str] = None  # None for global; the platform/equipment key otherwise
    role: Role

    @model_validator(mode="after")
    def _check_scope(self) -> "Grant":
        if self.scope == "global" and self.id:
            raise ValueError("global grant must not carry an id")
        if self.scope in ("platform", "equipment") and not self.id:
            raise ValueError(f"{self.scope} grant requires an id")
        if self.scope == "equipment" and self.role == "admin":
            raise ValueError("admin is not allowed at equipment scope (use platform or global)")
        return self


class PathPolicy(BaseModel):
    """Which edge paths a principal may reach at all (Phase 2).

    Grants answer *"may you use this equipment"*; they are service-level, so a
    grant on ``analytica_db`` opens every one of its routes at once. That is the
    right granularity for a human operator and the wrong one for a machine
    principal that should see raw data but not the scientific record — see
    ``docs/HERMES_ACCESS_DESIGN.md``.

    Semantics, deliberately default-deny:

    * A principal with **no** ``paths`` block is unrestricted (every human
      today), so this is purely additive.
    * With a ``paths`` block: ``deny`` wins outright, then the path must match
      an ``allow`` pattern, else the request is refused. A route added to a
      downstream service later therefore starts closed for path-scoped
      principals until someone opens it on purpose.

    Patterns are ``fnmatch`` globs against the **edge** path (``/analytica/…``,
    not the service-local path), because that is what the request carries when
    the edge asks us to authorize it.
    """

    model_config = ConfigDict(extra="forbid")

    allow: list[str] = []
    deny: list[str] = []

    @model_validator(mode="after")
    def _check_patterns(self) -> "PathPolicy":
        if not self.allow and not self.deny:
            raise ValueError("paths policy must list at least one allow or deny pattern")
        for pattern in [*self.allow, *self.deny]:
            if not pattern.startswith("/"):
                raise ValueError(f"path pattern must start with '/': {pattern!r}")
        return self


class RosterUser(BaseModel):
    """A human account on the allow-list."""

    model_config = ConfigDict(extra="forbid")

    email: str
    role: UserRole = "operator"
    name: str = ""
    lab_account: str = ""
    notes: str = ""
    expires: Optional[date] = None
    status: Literal["active", "disabled"] = "active"
    disabled_reason: str = ""
    grants: list[Grant] = []  # resolved by authz.effective_central_role
    # Optional edge-path restriction; absent => unrestricted. See PathPolicy.
    paths: Optional[PathPolicy] = None

    @field_validator("email")
    @classmethod
    def _norm_email_field(cls, v: str) -> str:
        return _validate_email(v)

    @field_validator("role", mode="before")
    @classmethod
    def _role_alias(cls, v: object) -> object:
        # accept the legacy DB value "user" as a synonym for "operator"
        return "operator" if v == "user" else v

    @property
    def expires_at(self) -> Optional[float]:
        return _end_of_day_epoch(self.expires) if self.expires else None

    @property
    def is_expired(self) -> bool:
        at = self.expires_at
        return at is not None and _now() > at

    @property
    def is_active(self) -> bool:
        return self.status == "active" and not self.is_expired


class RosterAutomation(BaseModel):
    """A machine principal (device role ``automation``). API-key secrets are NEVER
    in this file — they live hashed in the SQLite ``api_keys`` table; this entry
    only declares the account exists and whether an admin has approved it."""

    model_config = ConfigDict(extra="forbid")

    email: str
    name: str = ""
    approved: bool = False  # global-admin approval gate (requirement 5)
    # Scope. `platform` is shorthand for a single platform-scoped grant; use
    # `grants` for anything narrower (an account that only drives one device
    # should say so at equipment scope). Both are consulted by
    # authz.effective_device_role, which bounds the account to what it declares.
    # An account declaring NEITHER is lab-wide — the pre-scope default, kept for
    # back-compat. The `role` on an automation grant is vestigial: the device
    # role is always `automation`; only the scope/id are read.
    platform: Optional[str] = None
    grants: list[Grant] = []
    expires: Optional[date] = None
    notes: str = ""
    # Optional edge-path restriction; absent => unrestricted. See PathPolicy.
    # Machine principals are the main consumer: a service-level grant is too
    # coarse for an agent that may read raw data but not the record built on it.
    paths: Optional[PathPolicy] = None

    @field_validator("email")
    @classmethod
    def _norm_email_field(cls, v: str) -> str:
        return _validate_email(v)

    @property
    def expires_at(self) -> Optional[float]:
        return _end_of_day_epoch(self.expires) if self.expires else None

    @property
    def is_expired(self) -> bool:
        at = self.expires_at
        return at is not None and _now() > at

    @property
    def is_active(self) -> bool:
        return self.approved and not self.is_expired


class RosterProject(BaseModel):
    """A project — the unit of data ownership. ``pis`` (one or more) **own** the
    project's data (and get the bill); ``members`` are the team who may consume it
    while the project is active. A user may belong to **many** projects, so no one
    has to "leave" a group to join another. Used for data access only — never for
    hardware grants. ``status: closed`` keeps the data (owners/admin still read)
    but stops members from reading it."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = ""
    status: Literal["active", "closed"] = "active"
    pis: list[str] = []
    members: list[str] = []

    @field_validator("id")
    @classmethod
    def _norm_id(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("project id must be non-empty")
        return v

    @field_validator("pis", "members")
    @classmethod
    def _norm_emails(cls, v: list[str]) -> list[str]:
        return [_validate_email(e) for e in v]

    @model_validator(mode="after")
    def _require_pi(self) -> "RosterProject":
        if not self.pis:
            raise ValueError(f"project {self.id!r} must have at least one PI")
        return self


def cross_check_ids(
    roster: "Roster",
    *,
    equipment_ids: "Iterable[str] | None",
    platform_ids: "Iterable[str] | None",
) -> list[str]:
    """Referential integrity between the roster and the registries it names.

    Grant matching is exact string equality (``authz._grant_applies``), so an id
    that exists nowhere is a **silently dead grant** — the account simply never
    gets the access the file appears to give it (this is how the
    ``la_agente_analitica`` typo went unnoticed until 2026-07-24). Grants are
    the only cross-file references the schema cannot see: project pis/members
    are already rejected at load time against the roster's own users.

    Pure — callers supply the known-id sets (the CLI loads them from
    ``equipment.yaml`` / ``platforms.yaml``); this module stays free of registry
    file formats. Pass ``None`` for a set to skip that check (registry file not
    available) — distinct from an *empty* set, which means the registry exists
    and every grant against it is dead. Returns human-readable errors, empty
    when everything resolves.
    """
    eq = None if equipment_ids is None else set(equipment_ids)
    plat = None if platform_ids is None else set(platform_ids)
    errors: list[str] = []

    def _check_grants(who: str, grants: list[Grant]) -> None:
        for g in grants:
            if g.scope == "equipment" and eq is not None and g.id not in eq:
                errors.append(
                    f"{who}: equipment grant {g.id!r} matches no equipment.yaml id (dead grant)"
                )
            elif g.scope == "platform" and plat is not None and g.id not in plat:
                errors.append(
                    f"{who}: platform grant {g.id!r} matches no platforms.yaml section (dead grant)"
                )

    for u in roster.users:
        _check_grants(u.email, u.grants)
    for a in roster.automation:
        _check_grants(a.email, a.grants)
        if a.platform and plat is not None and a.platform not in plat:
            errors.append(
                f"{a.email}: platform: {a.platform!r} matches no platforms.yaml section (dead scope)"
            )
    return errors


def _is_global_admin(u: RosterUser) -> bool:
    """A flat ``role: admin`` or an explicit ``{scope: global, role: admin}`` grant."""
    if u.role == "admin":
        return True
    return any(g.scope == "global" and g.role == "admin" for g in u.grants)


class Roster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[RosterUser] = []
    automation: list[RosterAutomation] = []
    projects: list[RosterProject] = []

    @model_validator(mode="after")
    def _unique_emails(self) -> "Roster":
        # structural invariant enforced on every construction
        seen: set[str] = set()
        dupes: list[str] = []
        for entry in [*self.users, *self.automation]:
            if entry.email in seen:
                dupes.append(entry.email)
            seen.add(entry.email)
        if dupes:
            raise ValueError("duplicate email: " + ", ".join(dupes))
        return self

    @model_validator(mode="after")
    def _validate_projects(self) -> "Roster":
        # Project ids unique; every PI and member must be a known account.
        # Additive: a roster with no projects is unaffected.
        ids: set[str] = set()
        for p in self.projects:
            if p.id in ids:
                raise ValueError(f"duplicate project id: {p.id}")
            ids.add(p.id)
        known = {u.email for u in self.users}
        for p in self.projects:
            for who in (*p.pis, *p.members):
                if who not in known:
                    raise ValueError(f"project {p.id!r} references unknown user: {who}")
        return self

    def has_active_admin(self) -> bool:
        """Lockout protection: at least one active *global* admin must remain — a
        flat ``role: admin`` or a ``{scope: global, role: admin}`` grant. (A
        platform/equipment admin grant does NOT count: only a global admin governs
        the allow-list.)"""
        return any(u.is_active and _is_global_admin(u) for u in self.users)

    # ---- helpers ----------------------------------------------------------

    def active_account_emails(self) -> set[str]:
        """Emails that currently have access (active humans + approved automation)
        — the set the mass-change guard compares across a reload."""
        emails = {u.email for u in self.users if u.is_active}
        emails |= {a.email for a in self.automation if a.is_active}
        return emails

    def user(self, email: str) -> Optional[RosterUser]:
        e = _norm_email(email)
        return next((u for u in self.users if u.email == e), None)

    def path_policy(self, email: str) -> Optional[PathPolicy]:
        """The edge-path restriction for a principal, human **or machine**.

        Machine principals live in ``automation``, not ``users``, so looking
        only at ``users`` would silently leave every agent unrestricted — which
        is precisely the population this policy exists to bound.
        """
        e = _norm_email(email)
        entry = self.user(e) or next((a for a in self.automation if a.email == e), None)
        return getattr(entry, "paths", None) if entry is not None else None

    def project(self, project_id: str) -> Optional[RosterProject]:
        return next((p for p in self.projects if p.id == project_id), None)

    def pi_projects(self, email: str) -> set[str]:
        """Project ids for which ``email`` is a PI (data owner). Owners read their
        projects' data regardless of project status."""
        e = _norm_email(email)
        return {p.id for p in self.projects if e in p.pis}

    def member_projects(self, email: str) -> set[str]:
        """Project ids ``email`` may consume as a team member: an **active** member
        of an **active** project. (The caller's account must also be active — that
        is the principal's own status, checked at the auth layer.)"""
        e = _norm_email(email)
        return {p.id for p in self.projects if p.status == "active" and e in p.members}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def default_roster_path() -> Path:
    override = os.environ.get("AUTH_ROSTER_PATH")
    if override:
        return Path(override)
    # ac_auth/roster.py -> ac_auth -> auth/roster.yaml
    return Path(__file__).resolve().parents[1] / "roster.yaml"


def _format_validation_error(exc: ValidationError) -> list[str]:
    msgs: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = err.get("msg", "invalid")
        msgs.append(f"{loc}: {msg}" if loc else msg)
    return msgs


def load_roster(path: Optional[Path | str] = None) -> Roster:
    """Parse + validate the roster file. Raises :class:`RosterError` on any
    problem (missing file, bad YAML, schema, or invariant). Use at startup so a
    broken roster *fails closed*."""
    p = Path(path) if path else default_roster_path()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise RosterError([f"cannot read roster {p}: {exc}"]) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RosterError([f"invalid YAML in {p}: {exc}"]) from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise RosterError([f"{p}: top level must be a mapping with 'users:'"])
    try:
        roster = Roster(**data)
    except ValidationError as exc:
        raise RosterError(_format_validation_error(exc)) from exc
    # Lockout protection: a *loaded* roster must keep at least one active admin.
    # (Enforced here rather than on the model so tests/tools can build partial
    # rosters in memory; every real file goes through this path.)
    if not roster.has_active_admin():
        raise RosterError(["no active admin remaining — refusing (lockout protection)"])
    return roster


@dataclass
class ReloadResult:
    roster: Roster  # the roster now in effect (the new one if applied, else current)
    applied: bool
    errors: list[str]  # why it was not applied (empty when applied)


def _default_max_revocations() -> int:
    try:
        return int(os.environ.get("AUTH_ROSTER_MAX_REVOCATIONS", _DEFAULT_MAX_REVOCATIONS))
    except ValueError:
        return _DEFAULT_MAX_REVOCATIONS


def reload_roster(
    path: Optional[Path | str],
    current: Roster,
    *,
    max_revocations: Optional[int] = None,
    force: bool = False,
) -> ReloadResult:
    """Validate a candidate roster against the live one for a hot reload.

    **Keep-last-good:** any schema/invariant failure, or a mass-change breach,
    returns ``current`` unchanged (``applied=False``) with the reasons — the
    running service never drops to a broken or surprisingly-empty allow-list.
    """
    try:
        new = load_roster(path)
    except RosterError as exc:
        return ReloadResult(current, False, exc.errors)

    if not force:
        limit = max_revocations if max_revocations is not None else _default_max_revocations()
        lost = current.active_account_emails() - new.active_account_emails()
        if len(lost) > limit:
            return ReloadResult(
                current,
                False,
                [
                    f"refusing reload: {len(lost)} active accounts would be "
                    f"removed/disabled (limit {limit}) — re-run with force to override. "
                    f"Affected: {', '.join(sorted(lost))}"
                ],
            )
    return ReloadResult(new, True, [])


# ---------------------------------------------------------------------------
# Dumping (CLI export)
# ---------------------------------------------------------------------------

_HEADER = (
    "# roster.yaml — the authoritative allow-list. Edit -> validate -> commit -> reload.\n"
    "# Validate before committing:  python -m ac_auth.cli validate auth/roster.yaml\n"
    "# SQLite never holds the allow-list; API-key secrets are NEVER in this file.\n"
)


def _user_dict(u: RosterUser) -> dict:
    d: dict[str, object] = {"email": u.email, "role": u.role}
    if u.name:
        d["name"] = u.name
    if u.lab_account:
        d["lab_account"] = u.lab_account
    if u.notes:
        d["notes"] = u.notes
    if u.expires:
        d["expires"] = u.expires.isoformat()
    if u.status != "active":
        d["status"] = u.status
    if u.disabled_reason:
        d["disabled_reason"] = u.disabled_reason
    if u.grants:
        d["grants"] = [g.model_dump(exclude_none=True) for g in u.grants]
    return d


def _automation_dict(a: RosterAutomation) -> dict:
    d: dict[str, object] = {"email": a.email, "approved": a.approved}
    if a.name:
        d["name"] = a.name
    if a.platform:
        d["platform"] = a.platform
    if a.grants:
        d["grants"] = [g.model_dump(exclude_none=True) for g in a.grants]
    if a.expires:
        d["expires"] = a.expires.isoformat()
    if a.notes:
        d["notes"] = a.notes
    return d


def dump_roster(roster: Roster) -> str:
    """Render a roster to YAML (with a header comment). Round-trips through
    :func:`load_roster`."""
    out: dict[str, object] = {"users": [_user_dict(u) for u in roster.users]}
    if roster.automation:
        out["automation"] = [_automation_dict(a) for a in roster.automation]
    body = yaml.safe_dump(out, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return _HEADER + body


__all__ = [
    "Role",
    "GrantScope",
    "Grant",
    "RosterUser",
    "RosterAutomation",
    "Roster",
    "RosterError",
    "ReloadResult",
    "load_roster",
    "reload_roster",
    "dump_roster",
    "default_roster_path",
    "cross_check_ids",
]
