"""Authorization seam: central account → device role.

This is the **single** place that resolves a user/principal to the role a device
enforces (``user`` / ``automation`` / ``service`` — capability-named; see the
device's ``control/roster.py``). Edge wiring (the ``/equipment/{key}/roster``
projection, ``/authz/check``, ``api/app/control.py``) calls only
:func:`effective_device_role` / :func:`effective_central_role`, never the role
strings directly, so the resolution rule lives here alone.

Note the device roles are named by *capability* and are deliberately decoupled
from the central *org* roles (``operator`` / ``admin``): ``admin`` is an org
privilege that maps to the ``service`` capability on a device.

**Phase 1 (per-scope grants).** A principal's effective central role on an
equipment is the **highest applicable** of:

* the flat global role on the row (``user``/``operator`` or ``admin``), treated
  as a ``global`` grant, **plus**
* any ``grants`` entry that applies to this equipment — ``global`` (always),
  ``platform`` (when the equipment belongs to that platform, per
  `platforms.yaml` membership), or ``equipment`` (exact key match).

``operator < admin``. With no grants and no membership this reduces exactly to
the old flat behavior, so the change is backward-compatible. The mapping:

* automation account (robot/platform principal) → ``automation`` **on the
  equipment its roster entry declares** (``platform:`` or ``grants:``); ``None``
  elsewhere. An account declaring no scope is lab-wide — the pre-scope default.
* effective central ``admin``                    → ``service``
* effective central ``operator``                 → ``user``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Optional

from .db import User

# Mirrors agilent_hplcms_server/control/roster.py :: Role.
DeviceRole = Literal["user", "automation", "service"]
CentralRole = Literal["operator", "admin"]

# Higher wins.
_RANK: dict[str, int] = {"operator": 1, "admin": 2}


def _flat_central(role: str) -> Optional[CentralRole]:
    """The flat global role as a central role: ``admin``, ``operator`` (incl. the
    legacy ``user``), or ``None`` for ``role: none`` (no global access)."""
    if role == "admin":
        return "admin"
    if role == "none":
        return None
    return "operator"


def _grant_applies(grant, equipment_key: str, membership: Mapping[str, Iterable[str]]) -> bool:
    """Does a single grant (duck-typed: .scope/.id/.role) apply to this equipment?"""
    scope = getattr(grant, "scope", None)
    if scope == "global":
        return True
    if scope == "platform":
        return grant.id in set(membership.get(equipment_key, ()))
    if scope == "equipment":
        return grant.id == equipment_key
    return False


def _automation_in_scope(
    user: User, equipment_key: str, membership: Optional[Mapping[str, Iterable[str]]]
) -> bool:
    """Is this automation account declared for ``equipment_key``?

    An automation account carries its declared scope as grants (see
    ``main._automation_grants``: ``platform: hte`` becomes a platform grant, and
    an account may instead name single equipment). It reaches only what it
    declares — a camera-follow principal has no business holding control of the
    sealer.

    An account declaring **no** scope is lab-wide. That is the pre-scope default
    and is kept deliberately: tightening it would silently revoke access from any
    account whose roster entry predates the field, and a revocation should be an
    explicit roster edit, not a side effect of a code change.
    """
    declared = tuple(getattr(user, "grants", ()) or ())
    if not declared:
        return True
    # Normalize as effective_central_role does: membership is optional, and a
    # platform grant must then resolve to nothing rather than raise.
    return any(_grant_applies(g, equipment_key, membership or {}) for g in declared)


def effective_central_role(
    user: User,
    equipment_key: str,
    membership: Optional[Mapping[str, Iterable[str]]] = None,
) -> Optional[CentralRole]:
    """Highest central role this human holds on ``equipment_key``, across the flat
    global role + applicable grants. ``None`` means **no access** (a ``role: none``
    user with no grant applying to this equipment)."""
    membership = membership or {}
    best = _flat_central(user.role)  # flat role = an implicit global grant (or None)
    best_rank = _RANK.get(best or "", 0)
    for grant in getattr(user, "grants", ()) or ():
        rank = _RANK.get(getattr(grant, "role", ""), 0)
        if rank > best_rank and _grant_applies(grant, equipment_key, membership):
            best = grant.role  # type: ignore[assignment]
            best_rank = rank
    return best


def effective_device_role(
    user: User,
    equipment_key: str,
    membership: Optional[Mapping[str, Iterable[str]]] = None,
) -> Optional[DeviceRole]:
    """Resolve the device role this account holds on ``equipment_key``, or
    ``None`` if it has **no access** there (so callers can exclude it from that
    device's roster).

    ``membership`` is the ``equipment_key -> {platform_id}`` map (from
    `platforms.yaml`, via :func:`ac_auth.platforms.load_membership`); omit it and
    platform-scoped grants simply don't resolve (global/equipment still do).
    """
    if user.is_automation:
        return "automation" if _automation_in_scope(user, equipment_key, membership) else None
    central = effective_central_role(user, equipment_key, membership)
    if central is None:
        return None
    return "service" if central == "admin" else "user"


# ---------------------------------------------------------------------------
# Data-access scope (data plane — distinct from device-role resolution above)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataScope:
    """A caller's project-based data-access scope, consumed by the data plane's
    ``can_read`` (the dashboard's lab.db reads and the AnaliticaDB catalog).

    ``member_projects`` = projects the caller may consume as a team member (an
    active member of an active project); ``pi_projects`` = projects they are a PI
    of (owner — readable regardless of project status); ``is_admin`` = global
    admin (governance read over all data). This is about **data ownership**, not
    hardware control — a PI may own data on a platform they cannot operate, and a
    user can be in many projects at once.
    """

    member_projects: frozenset[str]
    pi_projects: frozenset[str]
    is_admin: bool


def data_scope(
    user: User,
    *,
    member_projects: Iterable[str],
    pi_projects: Iterable[str],
) -> DataScope:
    """Project a principal to its data scope. ``member_projects`` / ``pi_projects``
    are supplied by the caller (from ``Roster.member_projects`` / ``pi_projects``)
    so this seam stays decoupled from the roster-file model — mirroring how the
    role resolver takes ``membership`` rather than importing ``platforms.yaml``.
    The activeness gates (account / membership / project) are already applied when
    those sets are computed."""
    # is_admin mirrors roster._is_global_admin (flat admin or a global admin grant).
    is_admin = user.role == "admin" or any(
        getattr(g, "scope", None) == "global" and getattr(g, "role", None) == "admin"
        for g in (getattr(user, "grants", ()) or ())
    )
    return DataScope(
        member_projects=frozenset(member_projects),
        pi_projects=frozenset(pi_projects),
        is_admin=is_admin,
    )


def path_permitted(policy, uri: str) -> bool:
    """Is ``uri`` reachable by a principal carrying ``policy`` (Phase 2)?

    ``policy`` is a duck-typed ``.allow`` / ``.deny`` pattern holder
    (``roster.PathPolicy``) or ``None``. ``None`` means unrestricted, so every
    principal without a ``paths:`` block behaves exactly as before.

    Why this exists: grants are **service-level**. A grant on ``analytica_db``
    opens all 24 of its routes, which is right for a human operator and wrong
    for a machine principal that may read raw measurements but not the
    experiment design or analysis behind them. See
    ``docs/HERMES_ACCESS_DESIGN.md``.

    Rules, in order — **deny wins, then allow, else refuse**:

    1. no policy                       → permitted
    2. matches any ``deny`` pattern    → refused (even if it also matches allow)
    3. matches any ``allow`` pattern   → permitted
    4. otherwise                       → refused

    Step 4 is the load-bearing one: a route added to a downstream service later
    is closed for path-scoped principals until it is opened deliberately. The
    alternative (default-allow) would silently widen every such principal each
    time someone adds an endpoint.

    The query string is ignored — patterns match the path only, so a policy
    cannot be evaded with ``?``, and cannot accidentally depend on parameters.
    """
    if policy is None:
        return True

    path = _normalize_path(uri)
    deny = list(getattr(policy, "deny", ()) or ())
    allow = list(getattr(policy, "allow", ()) or ())

    if any(_path_matches(path, pattern) for pattern in deny):
        return False
    return any(_path_matches(path, pattern) for pattern in allow)


def _normalize_path(uri: str) -> str:
    """Path portion of a request URI, percent-decoded, without dot segments.

    Decoding and collapsing ``..`` before matching is what stops
    ``/analytica/measurements/../plans`` or ``/analytica/%2e%2e/plans`` from
    slipping past a prefix pattern.
    """
    from posixpath import normpath
    from urllib.parse import unquote, urlsplit

    path = urlsplit(uri or "").path
    # Decode repeatedly: a single pass leaves %252e ("%2e" re-encoded) intact.
    for _ in range(3):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    path = path.replace("\\", "/")
    if not path.startswith("/"):
        path = "/" + path
    collapsed = normpath(path)
    # normpath drops a meaningful trailing slash; keep the distinction.
    if path.endswith("/") and not collapsed.endswith("/"):
        collapsed += "/"
    return collapsed


def _path_matches(path: str, pattern: str) -> bool:
    """``fnmatch`` glob, plus the convention that a bare prefix matches its
    subtree — so ``/analytica/measurements`` covers ``/analytica/measurements/42``
    without every roster entry needing a ``*`` suffix."""
    from fnmatch import fnmatchcase

    if fnmatchcase(path, pattern):
        return True
    if not any(ch in pattern for ch in "*?["):
        base = pattern.rstrip("/")
        return path == base or path.startswith(base + "/")
    return False


__all__ = [
    "DeviceRole",
    "CentralRole",
    "effective_central_role",
    "effective_device_role",
    "path_permitted",
]
