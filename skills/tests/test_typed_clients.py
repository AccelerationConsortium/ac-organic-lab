"""Per-kind typed-client tests.

Exercises:

* ``LabSession.get`` / ``role`` returns the right per-kind subclass keyed on
  ``entry.kind``.
* Each typed method posts to the catalog endpoint and serialises args via
  the catalog's Pydantic schema.
* Out-of-range args raise locally (via Pydantic) before any HTTP round-trip.
* The plate-sealer acceptance call ``role.seal_start(temperature_c=170,
  seconds=3.0)`` works end-to-end against a respx-mocked PlateLoc surface.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from lab_skills import (
    EquipmentClient,
    EquipmentEntry,
    FumeHoodClient,
    Lab,
    PlateSealerClient,
    PressClient,
    Registry,
    RobotArmClient,
    SolidDoserClient,
)


def _entry(eid: str, *, kind: str, base_url: str) -> EquipmentEntry:
    return EquipmentEntry(
        id=eid,
        name=eid.title(),
        platform="hte",
        kind=kind,  # type: ignore[arg-type]
        adapter="http",
        base_url=base_url,
        poll_timeout_seconds=1.0,
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_plate_sealer_client_for_plate_sealer_kind() -> None:
    entry = _entry("plateloc", kind="plate_sealer", base_url="http://p.test:8000")
    registry = Registry(equipment=[entry])
    async with Lab.connect(registry=registry) as lab:
        client = lab.get(entry.id)
    assert isinstance(client, PlateSealerClient)
    assert isinstance(client, EquipmentClient)


@pytest.mark.asyncio
async def test_get_returns_press_client_for_press_kind() -> None:
    entry = _entry("press", kind="press", base_url="http://p.test:8000")
    registry = Registry(equipment=[entry])
    async with Lab.connect(registry=registry) as lab:
        assert isinstance(lab.get(entry.id), PressClient)


@pytest.mark.asyncio
async def test_get_returns_solid_doser_client_for_solid_doser_kind() -> None:
    entry = _entry("doser", kind="solid_doser", base_url="http://d.test:8000")
    registry = Registry(equipment=[entry])
    async with Lab.connect(registry=registry) as lab:
        assert isinstance(lab.get(entry.id), SolidDoserClient)


@pytest.mark.asyncio
async def test_get_returns_fume_hood_client_for_fume_hood_kind() -> None:
    entry = _entry("hood", kind="fume_hood", base_url="http://h.test:8000")
    registry = Registry(equipment=[entry])
    async with Lab.connect(registry=registry) as lab:
        assert isinstance(lab.get(entry.id), FumeHoodClient)


@pytest.mark.asyncio
async def test_get_returns_robot_arm_client_for_robot_arm_kind() -> None:
    entry = _entry("xarm", kind="robot_arm", base_url="http://x.test:8000")
    registry = Registry(equipment=[entry])
    async with Lab.connect(registry=registry) as lab:
        client = lab.get(entry.id)
    assert isinstance(client, RobotArmClient)
    # No control methods exposed in v0.2.
    assert not hasattr(client, "move")


@pytest.mark.asyncio
async def test_get_falls_back_to_base_client_for_kinds_without_wrapper() -> None:
    entry = _entry("ot2", kind="liquid_handler", base_url="http://o.test:8000")
    registry = Registry(equipment=[entry])
    async with Lab.connect(registry=registry) as lab:
        client = lab.get(entry.id)
    # Plain base class for kinds without a typed wrapper. It still has
    # status() / probe() / health() / command().
    assert type(client) is EquipmentClient


@pytest.mark.asyncio
async def test_role_returns_typed_client() -> None:
    """The acceptance-call surface: ``lab.role("sealer")`` returns a
    ``PlateSealerClient`` so ``.seal_start(...)`` is available.
    """

    entry = _entry("plateloc", kind="plate_sealer", base_url="http://p.test:8000")
    registry = Registry(equipment=[entry])
    async with Lab.connect(
        registry=registry, binding={"sealer": entry.id}
    ) as lab:
        sealer = lab.role("sealer")
    assert isinstance(sealer, PlateSealerClient)


# ---------------------------------------------------------------------------
# PlateSealerClient (the live-acceptance target)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plate_sealer_seal_start_acceptance() -> None:
    """Reproduces the v0.2 acceptance call from the plan:

        await lab.role("sealer").seal_start(temperature_c=170, seconds=3.0)
    """

    entry = _entry("plateloc", kind="plate_sealer", base_url="http://p.test:8000")
    registry = Registry(equipment=[entry])
    captured: dict = {}

    def _record(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "message": "Seal cycle started"})

    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/start").mock(side_effect=_record)
        async with Lab.connect(
            registry=registry, binding={"sealer": entry.id}
        ) as lab:
            result = await lab.role("sealer").seal_start(
                temperature_c=170, seconds=3.0
            )

    assert captured["json"] == {"temperature_c": 170, "seconds": 3.0}
    assert result == {"ok": True, "message": "Seal cycle started"}


@pytest.mark.asyncio
async def test_plate_sealer_seal_start_validates_args_locally() -> None:
    """Out-of-range args fail in the catalog schema BEFORE any HTTP request."""

    from pydantic import ValidationError

    entry = _entry("plateloc", kind="plate_sealer", base_url="http://p.test:8000")
    registry = Registry(equipment=[entry])

    with respx.mock(base_url=entry.base_url, assert_all_called=False) as router:
        # Mock that we expect NOT to call - args fail Pydantic before HTTP.
        route = router.post("/control/seal/start").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        async with Lab.connect(registry=registry) as lab:
            sealer = lab.get(entry.id)
            with pytest.raises(ValidationError):
                await sealer.seal_start(temperature_c=10, seconds=3.0)  # too low
        assert route.call_count == 0


@pytest.mark.asyncio
async def test_plate_sealer_other_methods_round_trip() -> None:
    entry = _entry("plateloc", kind="plate_sealer", base_url="http://p.test:8000")
    registry = Registry(equipment=[entry])

    with respx.mock(base_url=entry.base_url) as router:
        startup = router.post("/control/startup").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        stage_in = router.post("/control/stage/in").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        stage_out = router.post("/control/stage/out").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        seal_stop = router.post("/control/seal/stop").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        set_temp = router.post("/control/seal/temperature").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        set_time = router.post("/control/seal/time").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        async with Lab.connect(registry=registry) as lab:
            sealer = lab.get(entry.id)
            await sealer.startup(profile="default")
            await sealer.stage_in()
            await sealer.stage_out()
            await sealer.seal_stop()
            await sealer.set_sealing_temperature(temperature_c=170)
            await sealer.set_sealing_time(seconds=3.0)

    for r in (startup, stage_in, stage_out, seal_stop, set_temp, set_time):
        assert r.call_count == 1


# ---------------------------------------------------------------------------
# Other kinds (smoke coverage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_press_init_round_trip() -> None:
    entry = _entry("press", kind="press", base_url="http://pr.test:8000")
    registry = Registry(equipment=[entry])
    with respx.mock(base_url=entry.base_url) as router:
        route = router.post("/init").mock(
            return_value=httpx.Response(200, json={"equipment_status": "ok"})
        )
        async with Lab.connect(registry=registry) as lab:
            await lab.get(entry.id).init()
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_press_press_up_passes_hold_time_via_query_string() -> None:
    """Legacy press accepts ``hold_time`` as a URL query param, not a body."""

    entry = _entry("press", kind="press", base_url="http://pr.test:8000")
    registry = Registry(equipment=[entry])
    with respx.mock(base_url=entry.base_url) as router:
        route = router.post(
            "/press/up", params={"hold_time": "1.5"}
        ).mock(return_value=httpx.Response(200, json={"equipment_status": "ok"}))
        async with Lab.connect(registry=registry) as lab:
            await lab.get(entry.id).press_up(hold_time=1.5)
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_solid_doser_dose_well_validates_target() -> None:
    from pydantic import ValidationError

    entry = _entry("doser", kind="solid_doser", base_url="http://d.test:8000")
    registry = Registry(equipment=[entry])
    with respx.mock(base_url=entry.base_url):
        async with Lab.connect(registry=registry) as lab:
            doser = lab.get(entry.id)
            with pytest.raises(ValidationError):
                await doser.dose_well(well="A1", target_mg=-1.0)


@pytest.mark.asyncio
async def test_solid_doser_dose_well_round_trip() -> None:
    entry = _entry("doser", kind="solid_doser", base_url="http://d.test:8000")
    registry = Registry(equipment=[entry])
    captured: dict = {}

    def _record(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "well": "A1",
                "position": [0.0, 0.0],
                "target_mg": 50.0,
            },
        )

    with respx.mock(base_url=entry.base_url) as router:
        router.post("/dose/well").mock(side_effect=_record)
        async with Lab.connect(registry=registry) as lab:
            await lab.get(entry.id).dose_well(well="A1", target_mg=50.0)

    assert captured["json"]["well"] == "A1"
    assert captured["json"]["target_mg"] == 50.0
    assert captured["json"]["verify"] is True
    assert captured["json"]["use_pid"] is False


@pytest.mark.asyncio
async def test_fume_hood_move_validates_position_range() -> None:
    from pydantic import ValidationError

    entry = _entry("hood", kind="fume_hood", base_url="http://h.test:8000")
    registry = Registry(equipment=[entry])
    async with Lab.connect(registry=registry) as lab:
        hood = lab.get(entry.id)
        with pytest.raises(ValidationError):
            await hood.move(position=7)  # out of 1..5


@pytest.mark.asyncio
async def test_fume_hood_move_round_trip() -> None:
    entry = _entry("hood", kind="fume_hood", base_url="http://h.test:8000")
    registry = Registry(equipment=[entry])
    captured: dict = {}

    def _record(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(202, json={"position": 3})

    with respx.mock(base_url=entry.base_url) as router:
        router.post("/move").mock(side_effect=_record)
        async with Lab.connect(registry=registry) as lab:
            await lab.get(entry.id).move(position=3)
    assert captured["json"] == {"position": 3}
