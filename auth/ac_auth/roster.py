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
from typing import Literal, Optional

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
    """A per-scope authorization (Phase 1). Parsed and validated now so the file
    format is stable; not consulted by the resolver until the hierarchy lands."""

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
    grants: list[Grant] = []  # Phase 1; parsed, not yet consulted

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
    platform: Optional[str] = None  # Phase 1 scope
    expires: Optional[date] = None
    notes: str = ""

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


def _is_global_admin(u: RosterUser) -> bool:
    """A flat ``role: admin`` or an explicit ``{scope: global, role: admin}`` grant."""
    if u.role == "admin":
        return True
    return any(g.scope == "global" and g.role == "admin" for g in u.grants)


class Roster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[RosterUser] = []
    automation: list[RosterAutomation] = []

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
]
