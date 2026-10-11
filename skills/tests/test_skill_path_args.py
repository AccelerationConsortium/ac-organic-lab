"""Skill endpoints with path arguments ({session_id}) and non-POST methods."""

from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel

from lab_skills.client import EquipmentClient
from lab_skills.registry import EquipmentEntry
from lab_skills.skill_catalog.models import SkillDef
from lab_skills.skill_catalog.registry import skills_for


class _Args(BaseModel):
    session_id: str
    seq: int


def _sd(endpoint, method="POST"):
    return SkillDef(name="x.y", kind="robot_arm", description="d", endpoint=endpoint,
                    method=method, args_schema=_Args)


def test_resolve_path_fills_quotes_and_strips():
    path, body = _sd("/control/t/{session_id}/chunks/{seq}").resolve_path(
        {"session_id": "ab/c d", "seq": 2, "points": [1]})
    assert path == "/control/t/ab%2Fc%20d/chunks/2" and body == {"points": [1]}


def test_resolve_path_refuses_a_missing_value():
    with pytest.raises(ValueError, match="session_id"):
        _sd("/control/t/{session_id}").resolve_path({"seq": 1})


def test_resolve_path_leaves_plain_endpoints_alone():
    assert _sd("/control/t").resolve_path({"a": 1}) == ("/control/t", {"a": 1})


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
async def test_client_command_sends_the_skill_method(method):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"], seen["path"], seen["body"] = request.method, request.url.path, request.content
        return httpx.Response(200, json={"ok": True})

    entry = EquipmentEntry(id="arm", name="arm", kind="robot_arm", adapter="http",
                           base_url="http://arm.test:8000", poll_timeout_seconds=1.0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = EquipmentClient(entry, http)
        assert await client.command("/control/t/s1", {"a": 1}, method=method) == {"ok": True}
    assert seen["method"] == method and seen["path"] == "/control/t/s1"
    assert (seen["body"] == b"") == (method == "GET")


def test_xarm_trajectory_skills_resolve():
    skills = {s.name: s for s in skills_for("robot_arm", "xarm_translocation")}
    assert skills["trajectory.limits"].method == "GET"
    assert {"trajectory.limits", "trajectory.validate", "trajectory.create", "trajectory.upload_chunk", "trajectory.start",
            "trajectory.status", "trajectory.cancel"} <= set(skills)
    path, body = skills["trajectory.upload_chunk"].resolve_path(
        {"session_id": "abc", "seq": 0, "points": [], "final": True})
    assert path == "/control/freehand/trajectory/abc/chunks/0" and body == {"points": [], "final": True}
    assert skills["trajectory.status"].method == "GET"
