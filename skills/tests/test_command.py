"""``EquipmentClient.command()`` tests with respx-mocked HTTP.

Covers the v0.2 control-side error mapping:

* 200 -> raw / typed response
* 409 -> :class:`EquipmentBusy`
* 400 / 422 with a "not initialized" detail -> :class:`RequiresInit`
* 400 / 422 with another detail -> :class:`BadRequest`
* 5xx, timeout, parse error -> :class:`EquipmentUnreachable`
"""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import BaseModel, Field

from ac_organic_lab_skills import (
    BadRequest,
    EquipmentBusy,
    EquipmentClient,
    EquipmentUnreachable,
    RequiresInit,
)
from ac_organic_lab_skills.registry import EquipmentEntry


def _entry(**overrides) -> EquipmentEntry:
    base = dict(
        id="test_dev",
        name="Test Device",
        platform="hte",
        kind="plate_sealer",
        adapter="http",
        base_url="http://device.local:8000",
        status_path="/status",
        poll_timeout_seconds=1.0,
    )
    base.update(overrides)
    return EquipmentEntry(**base)


@pytest.fixture
async def http():
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as c:
        yield c


class _SealStartArgs(BaseModel):
    """Minimal args schema, mirrors the v0.3 catalog entry for plate_sealer."""

    temperature_c: int = Field(ge=20, le=235)
    seconds: float = Field(ge=0.5, le=12.0)


class _CommandResponse(BaseModel):
    ok: bool = True
    message: str | None = None


@pytest.mark.asyncio
async def test_command_post_with_pydantic_body_serialises_json(http) -> None:
    entry = _entry()
    body = _SealStartArgs(temperature_c=170, seconds=3.0)
    captured: dict = {}

    def _record(request: httpx.Request) -> httpx.Response:
        import json

        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "message": "Seal cycle started"})

    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/start").mock(side_effect=_record)
        client = EquipmentClient(entry, http)
        result = await client.command("/control/seal/start", body)

    assert captured["json"] == {"temperature_c": 170, "seconds": 3.0}
    assert result == {"ok": True, "message": "Seal cycle started"}


@pytest.mark.asyncio
async def test_command_with_response_schema_returns_typed_model(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/start").mock(
            return_value=httpx.Response(200, json={"ok": True, "message": "ok"})
        )
        client = EquipmentClient(entry, http)
        result = await client.command(
            "/control/seal/start",
            {"temperature_c": 170, "seconds": 3.0},
            response_schema=_CommandResponse,
        )
    assert isinstance(result, _CommandResponse)
    assert result.ok is True
    assert result.message == "ok"


@pytest.mark.asyncio
async def test_command_accepts_none_body(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/stop").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client = EquipmentClient(entry, http)
        result = await client.command("/control/seal/stop")
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_command_204_returns_none(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/stage/in").mock(
            return_value=httpx.Response(204)
        )
        client = EquipmentClient(entry, http)
        result = await client.command("/control/stage/in")
    assert result is None


@pytest.mark.asyncio
async def test_command_409_raises_equipment_busy(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/start").mock(
            return_value=httpx.Response(
                409, json={"detail": "Cycle already in progress"}
            )
        )
        client = EquipmentClient(entry, http)
        with pytest.raises(EquipmentBusy) as exc_info:
            await client.command("/control/seal/start", {"temperature_c": 170})
    assert exc_info.value.equipment_id == entry.id
    assert exc_info.value.http_status == 409
    assert "in progress" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_command_400_not_connected_raises_requires_init(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/start").mock(
            return_value=httpx.Response(
                400,
                json={
                    "detail": (
                        "PlateLoc is not connected. POST /control/startup first."
                    )
                },
            )
        )
        client = EquipmentClient(entry, http)
        with pytest.raises(RequiresInit) as exc_info:
            await client.command("/control/seal/start", {"temperature_c": 170})
    assert exc_info.value.equipment_id == entry.id
    assert exc_info.value.http_status == 400
    assert "not connected" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_command_409_not_connected_still_busy(http) -> None:
    """When the device returns 409, the SDK reports ``EquipmentBusy`` even if
    the body mentions "not connected" - the HTTP code wins. Ensures we don't
    misclassify devices (like ``agilent_plateloc``) that fold "not connected"
    into 409 rather than 400.
    """

    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/start").mock(
            return_value=httpx.Response(
                409,
                json={
                    "detail": (
                        "PlateLoc is not connected. POST /control/startup first."
                    )
                },
            )
        )
        client = EquipmentClient(entry, http)
        with pytest.raises(EquipmentBusy):
            await client.command("/control/seal/start", {"temperature_c": 170})


@pytest.mark.asyncio
async def test_command_400_other_raises_bad_request(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/start").mock(
            return_value=httpx.Response(
                400, json={"detail": "Unknown profile 'banana'"}
            )
        )
        client = EquipmentClient(entry, http)
        with pytest.raises(BadRequest) as exc_info:
            await client.command("/control/seal/start", {"temperature_c": 170})
    assert exc_info.value.http_status == 400
    assert "banana" in exc_info.value.message


@pytest.mark.asyncio
async def test_command_422_validation_raises_bad_request(http) -> None:
    """FastAPI returns 422 for Pydantic validation failures; treated as a
    structured ``BadRequest`` so workflow code does not have to parse the
    nested validation list."""

    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/start").mock(
            return_value=httpx.Response(
                422,
                json={
                    "detail": [
                        {
                            "loc": ["body", "temperature_c"],
                            "msg": "ensure this value is greater than or equal to 20",
                            "type": "value_error.number.not_ge",
                        }
                    ]
                },
            )
        )
        client = EquipmentClient(entry, http)
        with pytest.raises(BadRequest) as exc_info:
            await client.command("/control/seal/start", {"temperature_c": 5})
    assert exc_info.value.http_status == 422


@pytest.mark.asyncio
async def test_command_5xx_raises_unreachable(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/start").mock(return_value=httpx.Response(503))
        client = EquipmentClient(entry, http)
        with pytest.raises(EquipmentUnreachable):
            await client.command("/control/seal/start", {"temperature_c": 170})


@pytest.mark.asyncio
async def test_command_timeout_raises_unreachable(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/start").mock(
            side_effect=httpx.TimeoutException("timed out")
        )
        client = EquipmentClient(entry, http)
        with pytest.raises(EquipmentUnreachable) as exc_info:
            await client.command("/control/seal/start", {"temperature_c": 170})
    assert "timeout" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_command_unparseable_response_raises_unreachable(http) -> None:
    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/start").mock(
            return_value=httpx.Response(200, content=b"not json")
        )
        client = EquipmentClient(entry, http)
        with pytest.raises(EquipmentUnreachable):
            await client.command("/control/seal/start", {"temperature_c": 170})


@pytest.mark.asyncio
async def test_command_no_base_url_raises_unreachable(http) -> None:
    entry = _entry(base_url=None)
    client = EquipmentClient(entry, http)
    with pytest.raises(EquipmentUnreachable):
        await client.command("/control/seal/start", {"temperature_c": 170})


@pytest.mark.asyncio
async def test_command_relative_path_without_leading_slash(http) -> None:
    """``command()`` should accept paths with or without a leading slash and
    produce the same URL.
    """

    entry = _entry()
    with respx.mock(base_url=entry.base_url) as router:
        route = router.post("/control/seal/stop").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client = EquipmentClient(entry, http)
        await client.command("control/seal/stop")
    assert route.call_count == 1
