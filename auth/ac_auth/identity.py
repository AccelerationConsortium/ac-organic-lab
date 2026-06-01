"""Tailscale identity resolution via the local ``tailscaled`` LocalAPI.

How identity is discovered
--------------------------
Every connection over the Tailnet has a stable source IP (``100.64.x.y`` /
``fd7a:…``). ``tailscaled`` knows which Tailscale **node** owns that IP and,
for a node signed in as a person, which **user** owns that node. We ask it via
the LocalAPI ``whois`` endpoint over the daemon's unix socket:

    GET http://local-tailscaled.sock/localapi/v0/whois?addr=<source-ip>

which returns ``{UserProfile: {LoginName, DisplayName}, Node: {Name, Tags}}``.

- A **person's device** → ``UserProfile.LoginName`` is their SSO identity
  (e.g. ``alice@github``), ``Node.Tags`` empty.
- A **tagged node** (lab infrastructure — every device here carries
  ``tag:sdl2-devices``) → there is no human owner; ``LoginName``/``Node.Name``
  is the *machine* and ``Node.Tags`` lists the tags. :attr:`Identity.is_tagged`
  flags this so callers don't mistake a server for a user.

No new credentials: this reuses the same Tailscale SSO that already gates the
Tailnet. The sidecar must run on a host where ``tailscaled`` is present (the
dashboard host) and be able to read its socket.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger("ac_auth.identity")

# Default daemon socket on Linux. Override in tests / unusual installs.
DEFAULT_TAILSCALED_SOCKET = "/var/run/tailscale/tailscaled.sock"
# The Host part is ignored by the daemon but httpx needs a valid URL.
_WHOIS_URL = "http://local-tailscaled.sock/localapi/v0/whois"
_WHOIS_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class Identity:
    """Resolved identity for a Tailnet source address."""

    login: str          # UserProfile.LoginName — a person's SSO id, or a machine name when tagged
    display: str        # UserProfile.DisplayName
    node: str           # Node.Name (trailing dot stripped)
    tags: tuple[str, ...]  # Node.Tags, e.g. ("tag:sdl2-devices",)
    addr: str           # the source IP this was resolved from

    @property
    def is_tagged(self) -> bool:
        """True for tagged infrastructure nodes (no human owner). For those,
        ``login`` is the machine, not a person — callers should treat them
        differently from a real user."""
        return bool(self.tags)


class TailscaleIdentityResolver:
    """Resolves a Tailnet source IP → :class:`Identity` via the LocalAPI.

    The ``client`` is injectable so tests can supply an ``httpx`` MockTransport
    instead of a real unix-socket connection.
    """

    def __init__(
        self,
        socket_path: str = DEFAULT_TAILSCALED_SOCKET,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._socket_path = socket_path
        self._client = client
        self._owns_client = client is None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            transport = httpx.AsyncHTTPTransport(uds=self._socket_path)
            self._client = httpx.AsyncClient(transport=transport, timeout=_WHOIS_TIMEOUT_S)
        return self._client

    async def whois(self, addr: str) -> Optional[Identity]:
        """Resolve ``addr`` to an :class:`Identity`, or ``None`` when the
        daemon has no answer (unknown IP, non-Tailnet client, daemon
        unreachable). Never raises — auth resolution must degrade, not crash.
        """
        if not addr:
            return None
        client = self._ensure_client()
        try:
            resp = await client.get(_WHOIS_URL, params={"addr": addr})
        except httpx.HTTPError as exc:
            logger.warning("whois transport error for %s: %s", addr, exc)
            return None
        if resp.status_code != 200:
            logger.info("whois %s -> HTTP %s (no identity)", addr, resp.status_code)
            return None
        try:
            data = resp.json()
        except ValueError:
            logger.warning("whois %s -> non-JSON body", addr)
            return None

        profile = data.get("UserProfile") or {}
        node = data.get("Node") or {}
        login = profile.get("LoginName")
        if not login:
            return None
        return Identity(
            login=login,
            display=profile.get("DisplayName") or login,
            node=(node.get("Name") or "").rstrip("."),
            tags=tuple(node.get("Tags") or ()),
            addr=addr,
        )

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
