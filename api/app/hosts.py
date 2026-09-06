"""Host inventory for *Utils → Computers and Servers*.

``GET /api/hosts`` answers "which machines run the lab, and what listens on
each" — derived entirely from configs that already exist, so the page stops
hand-maintaining a copy:

- **Host identity** comes from the SSH console whitelist
  (:data:`app.ssh_console.SSH_HOSTS`), the one server-side inventory of the
  lab's host machines (a host is not equipment, so it has no registry entry).
- **Services** come from the equipment registry (``equipment.yaml``): every
  entry with a ``base_url`` is a process listening somewhere, and the URL's
  hostname names the machine. Registering a new service there puts its port on
  the host tile with no code change here or in ``web/``.

Entries whose hostname matches no whitelisted machine are grouped under
``other_hosts`` (the device Pis, mostly) so the page still shows every
service's port and domain the registry knows about.

This endpoint is deliberately **ungated**, unlike the admin-only
``/api/ssh/hosts`` (console banner facts): it exposes nothing
``/api/equipment`` doesn't already publish — every snapshot there carries its
``base_url``, and Tailscale hostnames are not secrets in this project (see
``lab_skills.registry``).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from lab_skills import Registry

from .ssh_console import SSH_HOSTS

# Additional network identities each whitelisted machine answers to, beyond
# the hostname in SSH_HOSTS. equipment.yaml reaches the same machine under
# several names — loopback (the aggregator runs on gaia, so gateway services
# bind 127.0.0.1 there), tailnet IPs, MagicDNS FQDNs. Matching also tries the
# first DNS label, so "sdl2-pc-03-cytation.tail6a1dd7.ts.net" finds the host
# whose hostname is "sdl2-pc-03-cytation" (and vice versa) without an alias.
HOST_ALIASES: dict[str, frozenset[str]] = {
    # gaia is the dashboard host itself: loopback-bound services (kasa-tapo,
    # bambu, bitácora, the API's own entry) and the 100.64.254.6 tailnet IP
    # (pypoe, kuma, auth, BitacoraDB, the edge paths) are all this machine.
    "gaia": frozenset({"localhost", "127.0.0.1", "100.64.254.6"}),
    "cytation-pc": frozenset({"100.64.254.16"}),
    "uplc-pc": frozenset({"100.64.254.19"}),
    # Tailnet IP and the lab-switch address (192.168.254.79, wired).
    "gibbie-pc": frozenset({"100.64.254.17", "192.168.254.79"}),
    # Tailnet, lab-switch and campus addresses of the Process Chemistry PC.
    "lle-pc": frozenset({"100.64.254.13", "192.168.254.5", "172.31.35.241"}),
}

#: Registry-id convention marking a ``sdl-lab-hostops`` agent entry. Its live
#: ``/status`` ``details`` (backend, services_whitelist, restartable,
#: probe_ports) are what the page renders as the host-ops panel.
_OPS_ID_PREFIX = "hostops_"


def _name_keys(hostname: str | None) -> set[str]:
    """Normalised match keys for a hostname: the full name and its first DNS
    label, lowercased. Empty for a missing hostname."""
    if not hostname:
        return set()
    hostname = hostname.lower()
    return {hostname, hostname.split(".", 1)[0]}


def _role(entry_id: str, kind: str) -> str:
    if entry_id.startswith(_OPS_ID_PREFIX):
        return "ops"
    return "service" if kind == "other" else "equipment"


def _service_info(entry: Any) -> dict[str, Any] | None:
    """One registry entry as a service row, or ``None`` if it has no
    ``base_url`` (mock placeholders awaiting hardware)."""
    if not entry.base_url:
        return None
    parts = urlsplit(entry.base_url)
    return {
        "id": entry.id,
        "name": entry.name,
        "kind": entry.kind,
        "role": _role(entry.id, entry.kind),
        "base_url": entry.base_url,
        "host": (parts.hostname or "").lower(),
        # None when the URL names no explicit port (edge paths like
        # /hermes/) — the page then labels the chip with the path instead.
        "port": parts.port,
        "path": parts.path,
        "adapter": entry.adapter,
        "protocol": entry.protocol,
        "enabled": entry.enabled,
    }


def group_hosts(registry: Registry) -> dict[str, Any]:
    """Group every reachable registry entry by the machine its ``base_url``
    points at. Pure — no I/O — so it is directly testable."""
    hosts: list[dict[str, Any]] = []
    keys_to_host: dict[str, dict[str, Any]] = {}
    for ssh_host in SSH_HOSTS:
        info = {
            "id": ssh_host.id,
            "label": ssh_host.label,
            "kind": ssh_host.kind,
            "hostname": ssh_host.hostname,
            "services": [],
        }
        hosts.append(info)
        for key in _name_keys(ssh_host.hostname) | {
            alias.lower() for alias in HOST_ALIASES.get(ssh_host.id, frozenset())
        }:
            keys_to_host[key] = info

    unlisted: dict[str, dict[str, Any]] = {}
    for entry in registry.equipment:
        service = _service_info(entry)
        if service is None:
            continue
        matched = None
        for key in _name_keys(service["host"]):
            matched = keys_to_host.get(key)
            if matched is not None:
                break
        if matched is not None:
            matched["services"].append(service)
        else:
            group = unlisted.setdefault(
                service["host"], {"hostname": service["host"], "services": []}
            )
            group["services"].append(service)

    return {
        "hosts": hosts,
        "other_hosts": [unlisted[h] for h in sorted(unlisted)],
    }


def build_hosts_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["hosts"])

    @router.get("/hosts")
    async def list_lab_hosts(request: Request) -> dict[str, Any]:
        """The lab's host machines with the services each runs, from config."""
        return group_hosts(request.app.state.registry)

    return router
