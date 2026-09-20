"""Host inventory loader — the typed view of the root ``hosts.yaml``.

``hosts.yaml`` is the fourth committed root YAML file, beside
``equipment.yaml`` (what hardware exists), ``platforms.yaml`` (how the UI
presents it) and ``locations.yaml`` (where a thing can be). It answers
**which machines run the lab**: their identity, the way in over ssh, and every
network address the equipment registry reaches each one by.

This module is the loader, shaped exactly like
:mod:`lab_skills.registry` and :mod:`lab_skills.platforms`: pydantic models,
three-step path resolution (argument → environment variable → the first
``hosts.yaml`` found walking up from this file), and fail-fast errors — a
missing file raises :class:`FileNotFoundError`, a schema violation raises
:class:`ValueError`. There is no fallback to defaults, for the same reason
those two have none: a half-loaded inventory is worse than a loud one.

**Why it lives in ``api/`` rather than in the SDK.** The other three root
files are parsed by ``lab_skills`` because the SDK itself consumes them. A
host is not equipment — it has no registry entry, no adapter and no
``/status`` (see :mod:`app.hosts`) — and nothing in ``lab_skills`` has a
concept of one. Both sources this file transcribes
(:data:`app.ssh_console.SSH_HOSTS`, :data:`app.hosts.HOST_ALIASES`) and both
eventual consumers (the SSH console whitelist and ``GET /api/hosts``) are in
``api/``, so the loader belongs here too — the same call ``api/app/
presentation.py`` already makes for the dashboard-only view of
``equipment.yaml``.

**Phase 1: nothing reads this at runtime.** ``SSH_HOSTS`` and ``HOST_ALIASES``
remain the values the running code uses. ``api/tests/test_host_registry.py``
asserts the YAML round-trips to exactly those Python literals, so the two
cannot drift while a later phase moves the readers over.

**No secrets.** Tailscale hostnames and tailnet IPs are not treated as secrets
in this project (the same rule ``equipment.yaml`` states), but nothing that
names or hints at a credential belongs in ``hosts.yaml``: the key file and any
per-host ssh options stay in the service user's ``~/.ssh/config``, which
``ssh.target`` merely names an alias in.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

#: The physical path a machine answers on. ``tailscale`` is the tailnet
#: (100.64.x) address every service is normally reached over; ``lab_switch``
#: the wired 192.168.254.x lab switch; ``campus_wifi`` a 172.31.x campus
#: address, which goes down with the campus network; ``loopback`` a service
#: bound on the dashboard host itself.
NetworkType = Literal["tailscale", "lab_switch", "campus_wifi", "loopback"]

#: Which block of *Utils → Computers and Servers* a machine belongs in.
#: Presentation only — every host in the file is equally on the console
#: whitelist. Mirrors ``SshHost.group``.
HostGroup = Literal["machine", "device"]


class HostSshProfile(BaseModel):
    """One way to open a session on a host: the default login shell, a tmux
    attach-or-create, a WSL shell on a Windows PC.

    Declared once at the top of the file and referenced by id from each host,
    because the same handful of profiles repeats across the fleet. ``args`` is
    the remote command appended after the ssh target — a server-side
    whitelist, never something a browser names. Mirrors
    ``app.ssh_console.SshProfile``.
    """

    id: str
    label: str
    args: list[str] = Field(default_factory=list)
    description: str


class HostSsh(BaseModel):
    """How the dashboard host opens a shell on this machine.

    ``target`` is an alias in the *service user's* ``~/.ssh/config`` (or a bare
    ``user@host`` it can reach): the config stanza carries the key file, any
    non-default port and per-host options, and none of that is recorded here.
    """

    user: str
    target: str
    shell: str
    #: Profile ids, in offer order; the first is the default.
    profiles: list[str]


class HostNetwork(BaseModel):
    """One address this machine answers to.

    Together with the host's ``hostname`` these are the names the equipment
    registry's ``base_url`` values resolve to — the same set
    ``app.hosts.HOST_ALIASES`` carries today.
    """

    type: NetworkType
    address: str
    #: The MagicDNS name for this address when it differs from the host's
    #: ``hostname`` (the dashboard host's tailnet name is not its hostname).
    magicdns: str | None = None
    note: str | None = None


class HostEntry(BaseModel):
    """One machine in ``hosts.yaml``. Mirrors ``app.ssh_console.SshHost``."""

    id: str
    label: str
    kind: str
    group: HostGroup = "machine"
    hostname: str
    ssh: HostSsh
    note: str
    networks: list[HostNetwork] = Field(default_factory=list)

    def aliases(self) -> frozenset[str]:
        """Every extra name this machine answers to — each network address
        plus any MagicDNS name. The ``hostname`` itself is not included:
        ``app.hosts`` already matches on it and its first DNS label."""
        names: set[str] = set()
        for network in self.networks:
            names.add(network.address)
            if network.magicdns:
                names.add(network.magicdns)
        return frozenset(names)


class HostsConfig(BaseModel):
    """Parsed ``hosts.yaml``."""

    ssh_profiles: list[HostSshProfile]
    hosts: list[HostEntry]

    @model_validator(mode="after")
    def _check_references(self) -> "HostsConfig":
        profile_ids = [p.id for p in self.ssh_profiles]
        duplicates = {p for p in profile_ids if profile_ids.count(p) > 1}
        if duplicates:
            raise ValueError(f"duplicate ssh_profiles ids: {sorted(duplicates)}")

        host_ids = [h.id for h in self.hosts]
        duplicates = {h for h in host_ids if host_ids.count(h) > 1}
        if duplicates:
            raise ValueError(f"duplicate host ids: {sorted(duplicates)}")

        known = set(profile_ids)
        for host in self.hosts:
            if not host.ssh.profiles:
                raise ValueError(f"host {host.id}: needs at least one ssh profile")
            unknown = [p for p in host.ssh.profiles if p not in known]
            if unknown:
                raise ValueError(
                    f"host {host.id}: unknown ssh profile id(s) {unknown}; "
                    f"declare them under ssh_profiles"
                )
            addresses = [n.address for n in host.networks]
            repeated = {a for a in addresses if addresses.count(a) > 1}
            if repeated:
                raise ValueError(
                    f"host {host.id}: repeated network address(es) {sorted(repeated)}"
                )
        return self

    def by_id(self, host_id: str) -> HostEntry | None:
        for host in self.hosts:
            if host.id == host_id:
                return host
        return None

    def profile(self, profile_id: str) -> HostSshProfile | None:
        for profile in self.ssh_profiles:
            if profile.id == profile_id:
                return profile
        return None

    def profiles_for(self, host: HostEntry) -> list[HostSshProfile]:
        """This host's profiles, resolved and in offer order (first is the
        default). Every id is known — the validator refused the file
        otherwise."""
        resolved = [self.profile(p) for p in host.ssh.profiles]
        return [p for p in resolved if p is not None]

    def aliases(self) -> dict[str, frozenset[str]]:
        """``{host id: extra names}`` for every host that has any, in the
        shape ``app.hosts.HOST_ALIASES`` uses."""
        return {h.id: h.aliases() for h in self.hosts if h.aliases()}


def _default_hosts_path() -> Path:
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "hosts.yaml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Could not locate hosts.yaml in any ancestor directory of "
        f"{here}; pass an explicit path to load_hosts() or set LAB_HOSTS_PATH."
    )


def load_hosts(path: str | os.PathLike | None = None) -> HostsConfig:
    """Load the host inventory from YAML.

    Path resolution order:

    1. Argument ``path``, if provided.
    2. ``LAB_HOSTS_PATH`` environment variable.
    3. The first ``hosts.yaml`` found by walking up from this module.
    """

    resolved: Path
    if path is not None:
        resolved = Path(path)
    elif os.environ.get("LAB_HOSTS_PATH"):
        resolved = Path(os.environ["LAB_HOSTS_PATH"])
    else:
        resolved = _default_hosts_path()

    if not resolved.exists():
        raise FileNotFoundError(f"Host inventory not found at {resolved}")

    with resolved.open("r") as f:
        data = yaml.safe_load(f) or {}

    try:
        return HostsConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid host inventory at {resolved}: {exc}") from exc
