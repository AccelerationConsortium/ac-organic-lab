"""Panel authentication and strict proxy boundaries; all upstreams are mocked."""

import httpx
import pytest
from fastapi import FastAPI
from lab_skills.registry import EquipmentEntry, Registry

from app import robot_motion


PREFIX = "/api/robot-motion/ligand_ur5e"


@pytest.fixture
def panel(monkeypatch):
    monkeypatch.setenv("AUTH_SERVICE_BASE", "http://auth.test")
    monkeypatch.setenv("DASHBOARD_CONTROL_OPEN", "1")  # must not bypass this gate
    app = FastAPI()
    app.state.registry = Registry(equipment=[EquipmentEntry(
        id="ligand_ur5e", name="UR5e Arm", kind="robot_arm", adapter="http",
        base_url="http://robot.test:8075",
    )])
    app.include_router(robot_motion.build_robot_motion_router())
    calls = []
    state = {"verified": 200, "allowed": True, "role": "operator"}

    def handler(request):
        calls.append(request)
        if request.url.host == "auth.test":
            if request.url.path == "/auth/verify":
                return httpx.Response(state["verified"], headers={"X-Auth-User": "viewer@example.test"})
            assert request.url.path == "/authz/check"
            assert request.url.params["user"] == "viewer@example.test"
            assert request.url.params["equipment"] == "ligand_ur5e"
            return httpx.Response(200, json={"allowed": state["allowed"], "central_role": state["role"]})
        assert request.url.host == "robot.test"
        if "upstream" in state:
            return state["upstream"](request)
        return httpx.Response(200, json={"executed": False})

    async def send(path="status", *, method="GET", headers=None, content=None):
        # Poisoned client defaults must never be forwarded to either service.
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"X-Api-Key": "must-not-forward", "Authorization": "Bearer secret"},
            cookies={"unexpected": "secret"}, auth=("bad", "secret"),
        ) as upstream:
            app.state.robot_motion_client = upstream
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dashboard.test") as browser:
                return await browser.request(method, f"{PREFIX}/{path}",
                    headers={"Cookie": "session=test", **(headers or {})}, content=content)

    return app, calls, state, send


async def test_verified_identity_only_and_no_device_credentials(panel):
    _, calls, _, send = panel
    r = await send(headers={"X-Auth-User": "admin@example.test", "X-Auth-Role": "admin", "X-Api-Key": "forged"})
    assert r.status_code == 200
    assert len(calls) == 3
    for call in calls:
        assert "authorization" not in call.headers
        assert "x-api-key" not in call.headers
        assert "x-auth-user" not in call.headers
        assert "x-auth-role" not in call.headers
    assert calls[0].headers["cookie"] == "session=test"
    assert calls[0].headers["x-forwarded-uri"] == f"{PREFIX}/status"
    assert "cookie" not in calls[1].headers
    assert "cookie" not in calls[2].headers
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"


@pytest.mark.parametrize("extra", [{}, {"X-Api-Key": "valid-machine-key"}, {"X-Auth-User": "admin@example.test"}])
async def test_cookie_required_even_with_control_open(panel, extra):
    _, calls, _, send = panel
    assert (await send(headers={"Cookie": "", **extra})).status_code == 401
    assert calls == []


@pytest.mark.parametrize("status,expected", [(401, 401), (403, 403), (302, 503), (500, 503)])
async def test_invalid_or_revoked_session_stops_before_equipment(panel, status, expected):
    _, calls, state, send = panel
    state["verified"] = status
    assert (await send("web/")).status_code == expected
    assert len(calls) == 1


@pytest.mark.parametrize("allowed,role", [(False, "operator"), (True, "automation"), ("true", "operator")])
async def test_equipment_permission_required(panel, allowed, role):
    _, calls, state, send = panel
    state.update(allowed=allowed, role=role)
    assert (await send()).status_code == 403
    assert len(calls) == 2


@pytest.mark.parametrize("path,method", [
    ("control/home", "POST"), ("control/claim", "POST"), ("graph", "POST"),
    ("graph/validate", "GET"), ("status", "DELETE"), ("status", "PUT"),
    ("web/pyxarm/main.js", "GET"), ("web/server.py", "GET"),
    ("web/%2e%2e/control/home", "GET"), ("status?url=http://other.test", "GET"),
    ("graph/preview/", "POST"), ("ws", "GET"),
])
async def test_only_fixed_paths_and_methods(panel, path, method):
    _, calls, _, send = panel
    assert (await send(path, method=method)).status_code in {404, 405}
    assert calls == []


@pytest.mark.parametrize("path,types", list(robot_motion._GET_TYPES.items()))
async def test_allowlisted_assets_and_reads(panel, path, types):
    _, calls, state, send = panel
    media = sorted(types)[0]
    text = '{}' if media == "application/json" else "test asset"
    state["upstream"] = lambda _: httpx.Response(200, text=text, headers={
        "Content-Type": media, "Set-Cookie": "must-not-return=secret", "X-Auth-User": "upstream",
    })
    r = await send(path)
    assert r.status_code == 200
    assert str(calls[-1].url) == f"http://robot.test:8075/{path}"
    assert "set-cookie" not in r.headers
    assert "x-auth-user" not in r.headers
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]


@pytest.mark.parametrize("path", ["graph/validate", "graph/preview"])
async def test_graph_calculation_only(panel, path):
    _, calls, _, send = panel
    r = await send(path, method="POST", content='{"graph": {}}', headers={
        "Content-Type": "application/json", "Origin": "http://dashboard.test",
    })
    assert r.status_code == 200
    assert r.json()["executed"] is False
    assert calls[-1].method == "POST"
    assert calls[-1].content == b'{"graph": {}}'


async def test_graph_post_through_next_rewrite_preserves_browser_origin(panel):
    _, _, _, send = panel
    r = await send("graph/validate", method="POST", content="{}", headers={
        "Content-Type": "application/json", "Host": "127.0.0.1:8001",
        "X-Forwarded-Host": "dashboard.test", "Origin": "http://dashboard.test",
    })
    assert r.status_code == 200


@pytest.mark.parametrize("content,headers,expected", [
    ('{}', {"Content-Type": "text/plain"}, 415),
    ('{}', {"Content-Encoding": "gzip"}, 415),
    ('{}', {"Origin": "http://evil.test"}, 403),
    ('{}', {"Origin": "null"}, 403),
    ('{}', {"Sec-Fetch-Site": "cross-site"}, 403),
    ('invalid', {}, 422), ('[]', {}, 422),
    ('x' * (256 * 1024 + 1), {}, 413),
])
async def test_rejected_graph_requests_do_not_reach_device(panel, content, headers, expected):
    _, calls, _, send = panel
    r = await send("graph/preview", method="POST", content=content,
                   headers={"Content-Type": "application/json", **headers})
    assert r.status_code == expected
    assert all(call.url.host == "auth.test" for call in calls)


@pytest.mark.parametrize("upstream", [
    lambda _: httpx.Response(302, headers={"Location": "http://other.test"}),
    lambda _: httpx.Response(200, text="<html>oops</html>", headers={"Content-Type": "text/html"}),
    lambda _: httpx.Response(200, text="invalid", headers={"Content-Type": "application/json"}),
    lambda _: httpx.Response(200, content=b'\xff', headers={"Content-Type": "application/json"}),
    lambda _: httpx.Response(200, content=b'', headers={"Content-Type": "application/json", "Content-Encoding": "gzip"}),
])
async def test_bad_upstream_fails_closed(panel, upstream):
    _, calls, state, send = panel
    state["upstream"] = upstream
    assert (await send()).status_code == 502
    assert len(calls) == 3


async def test_bounded_upstream(panel, monkeypatch):
    _, _, state, send = panel
    monkeypatch.setattr(robot_motion, "_MAX_RESPONSE", 20)
    state["upstream"] = lambda _: httpx.Response(200, json={"long": "x" * 21})
    assert (await send()).status_code == 502


async def test_upstream_validation_error_preserved(panel):
    _, _, state, send = panel
    state["upstream"] = lambda _: httpx.Response(422, json={"detail": "Invalid graph"})
    r = await send("graph/validate", method="POST", content="{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert r.json()["detail"] == "Invalid graph"


async def test_documentation_uses_existing_read_only_renderer(panel):
    _, calls, _, send = panel
    r = await send("docs")
    assert r.status_code == 303
    assert r.headers["location"] == "/api/equipment/ligand_ur5e/documentation/docs"
    assert len(calls) == 2


@pytest.mark.parametrize("registration", ["missing", "disabled"])
async def test_missing_or_disabled_equipment_never_proxied(panel, registration):
    app, calls, _, send = panel
    if registration == "missing":
        app.state.registry.equipment.clear()
    else:
        app.state.registry.equipment[0].enabled = False
    assert (await send()).status_code == 503
    assert len(calls) == 2


def test_router_registered():
    from app.main import app
    assert f"{PREFIX}/{{panel_path}}" in app.openapi()["paths"]
