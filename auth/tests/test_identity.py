"""Unit tests for the Tailscale identity resolver.

Uses ``httpx.MockTransport`` so no real ``tailscaled`` socket is needed.
"""

from __future__ import annotations

import httpx

from ac_auth.identity import TailscaleIdentityResolver


def _resolver(payload: dict, status: int = 200) -> TailscaleIdentityResolver:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return TailscaleIdentityResolver(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_resolves_human_identity():
    r = _resolver({
        "UserProfile": {"LoginName": "alice@github", "DisplayName": "Alice"},
        "Node": {"Name": "alice-laptop.tail6a1dd7.ts.net.", "Tags": None},
    })
    ident = await r.whois("100.64.0.9")
    assert ident is not None
    assert ident.login == "alice@github"
    assert ident.display == "Alice"
    assert ident.is_tagged is False
    assert ident.node == "alice-laptop.tail6a1dd7.ts.net"   # trailing dot stripped
    assert ident.addr == "100.64.0.9"


async def test_tagged_node_flagged():
    r = _resolver({
        "UserProfile": {"LoginName": "sdl2-server-gaia.tail6a1dd7.ts.net", "DisplayName": "sdl2-server-gaia"},
        "Node": {"Name": "gaia.", "Tags": ["tag:sdl2-devices"]},
    })
    ident = await r.whois("100.64.254.6")
    assert ident is not None
    assert ident.is_tagged is True
    assert ident.tags == ("tag:sdl2-devices",)


async def test_non_200_returns_none():
    assert await _resolver({}, status=404).whois("100.64.0.1") is None


async def test_missing_login_returns_none():
    assert await _resolver({"UserProfile": {}, "Node": {}}).whois("100.64.0.1") is None


async def test_empty_addr_returns_none():
    # No round-trip should be needed for an empty address.
    assert await _resolver({"UserProfile": {"LoginName": "x"}}).whois("") is None


async def test_transport_error_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("daemon socket unavailable")

    r = TailscaleIdentityResolver(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await r.whois("100.64.0.1") is None
