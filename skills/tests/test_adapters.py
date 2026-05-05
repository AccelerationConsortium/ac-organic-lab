"""Adapter behaviour tests using captured fixture payloads.

We use `respx` to mock outbound HTTP and feed each adapter a realistic body,
then assert the normalized envelope. These act as the "snapshot tests against
captured device payloads" called for in the plan.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from ac_organic_lab_skills.adapters import (
    HttpStatusAdapter,
    LegacyDoseEveryWellAdapter,
    LegacyFilterEveryWellAdapter,
    LegacyFumeHoodActuatorAdapter,
    LegacyXArmAdapter,
    MockAdapter,
)
from ac_organic_lab_skills.registry import EquipmentEntry


def _entry(**overrides) -> EquipmentEntry:
    base = dict(
        id="test_equipment",
        name="Test Equipment",
        platform="hte",
        kind="solid_doser",
        adapter="legacy_http",
        base_url="http://device.local:8000",
        status_path="/status",
        poll_timeout_seconds=1.0,
    )
    base.update(overrides)
    return EquipmentEntry(**base)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as c:
        yield c


# ---------------------------------------------------------------------------
# dose_every_well
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doser_requires_init(load_fixture, client) -> None:
    http_status, body = load_fixture("dose_every_well_requires_init")
    entry = _entry(id="dose_every_well", kind="solid_doser")
    adapter = LegacyDoseEveryWellAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(http_status, json=body))
        result = await adapter.fetch(client)

    assert result.error is None, "400 not_initialized should be a normal state, not a fetch error"
    assert result.status.equipment_status == "requires_init"
    assert result.status.required_actions == ["startup"]
    assert result.status.components["gantry"].connected is False
    assert result.status.components["solid_doser"].connected is False


@pytest.mark.asyncio
async def test_doser_ready(load_fixture, client) -> None:
    http_status, body = load_fixture("dose_every_well_ready")
    entry = _entry(id="dose_every_well", kind="solid_doser")
    adapter = LegacyDoseEveryWellAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(http_status, json=body))
        result = await adapter.fetch(client)

    assert result.error is None
    assert result.status.equipment_status == "ready"
    assert result.status.components["gantry"].connected
    assert result.status.components["solid_doser"].connected
    assert result.status.metrics["flow_rate"].value == 12.5
    assert result.status.metrics["flow_rate"].unit == "mg/s"
    assert result.status.details.get("config") == "with_cnc_solid_doser"


# ---------------------------------------------------------------------------
# filter_every_well
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_ready(load_fixture, client) -> None:
    http_status, body = load_fixture("filter_every_well_ready")
    entry = _entry(id="filter_every_well", kind="press")
    adapter = LegacyFilterEveryWellAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(http_status, json=body))
        result = await adapter.fetch(client)

    assert result.error is None
    assert result.status.equipment_status == "ready"
    assert result.status.components["press_valve"].state == "up"
    assert result.status.components["plate"].state == "out"
    # Network-identity fields must NOT be in the normalized envelope.
    assert "equipment_ip" not in result.status.details
    assert "equipment_tailscale" not in result.status.details


@pytest.mark.asyncio
async def test_filter_dry_run(load_fixture, client) -> None:
    http_status, body = load_fixture("filter_every_well_dry_run")
    entry = _entry(id="filter_every_well", kind="press")
    adapter = LegacyFilterEveryWellAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(http_status, json=body))
        result = await adapter.fetch(client)

    assert result.error is None
    assert result.status.equipment_status == "dry_run"


# ---------------------------------------------------------------------------
# fume_hood_actuator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fume_hood_ready(load_fixture, client) -> None:
    http_status, body = load_fixture("fume_hood_actuator_ready")
    entry = _entry(
        id="fume_hood_actuator",
        kind="fume_hood",
        status_path="/equipment/status",
    )
    adapter = LegacyFumeHoodActuatorAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/equipment/status").mock(
            return_value=httpx.Response(http_status, json=body)
        )
        result = await adapter.fetch(client)

    assert result.error is None
    assert result.status.equipment_status == "ready"
    assert result.status.metrics["sash_position"].value == 3
    assert result.status.metrics["target_position"].value == 3
    assert result.status.components["actuator"].state == "stationary"


@pytest.mark.asyncio
async def test_fume_hood_busy(load_fixture, client) -> None:
    http_status, body = load_fixture("fume_hood_actuator_busy")
    entry = _entry(
        id="fume_hood_actuator",
        kind="fume_hood",
        status_path="/equipment/status",
    )
    adapter = LegacyFumeHoodActuatorAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/equipment/status").mock(
            return_value=httpx.Response(http_status, json=body)
        )
        result = await adapter.fetch(client)

    assert result.status.equipment_status == "busy"
    assert result.status.metrics["sash_position"].value == 2
    assert result.status.metrics["target_position"].value == 4


# ---------------------------------------------------------------------------
# xarm_translocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xarm_disconnected(load_fixture, client) -> None:
    http_status, body = load_fixture("xarm_disconnected")
    entry = _entry(id="xarm_translocation", kind="robot_arm", do_not_call_connect=True)
    adapter = LegacyXArmAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(http_status, json=body))
        result = await adapter.fetch(client)

    assert result.error is None
    assert result.status.equipment_status == "requires_init"
    assert result.status.required_actions == ["connect"]
    # We must never have called POST /connect.
    assert all(call.request.method == "GET" for call in router.calls)


@pytest.mark.asyncio
async def test_xarm_connected_ready(load_fixture, client) -> None:
    http_status, body = load_fixture("xarm_connected_ready")
    entry = _entry(id="xarm_translocation", kind="robot_arm", do_not_call_connect=True)
    adapter = LegacyXArmAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(http_status, json=body))
        result = await adapter.fetch(client)

    assert result.status.equipment_status == "ready"
    assert result.status.components["arm"].connected
    assert result.status.components["arm"].state == "idle"


# ---------------------------------------------------------------------------
# spec-compliant http adapter (forward-compatible)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_status_adapter_passthrough(load_fixture, client) -> None:
    http_status, body = load_fixture("env_sensors_ready")
    entry = _entry(id="env_sensors", kind="environmental_sensor", adapter="http")
    adapter = HttpStatusAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(http_status, json=body))
        result = await adapter.fetch(client)

    assert result.error is None
    assert result.status.equipment_status == "ready"
    assert result.status.metrics["temperature"].value == 22.3
    assert result.status.metrics["humidity"].unit == "%RH"


@pytest.mark.asyncio
async def test_http_status_adapter_rejects_invalid_envelope(client) -> None:
    entry = _entry(id="env_sensors", kind="environmental_sensor", adapter="http")
    adapter = HttpStatusAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(
            return_value=httpx.Response(200, json={"hello": "world"})
        )
        result = await adapter.fetch(client)

    assert result.error is not None
    assert result.error.kind == "parse_error"
    assert result.status.equipment_status == "unknown"


# ---------------------------------------------------------------------------
# transport failure handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_status_adapter_timeout_classified(client) -> None:
    entry = _entry(id="env_sensors", kind="environmental_sensor", adapter="http")
    adapter = HttpStatusAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(side_effect=httpx.TimeoutException("timed out"))
        result = await adapter.fetch(client)

    assert result.error is not None
    assert result.error.kind == "timeout"
    assert result.status.equipment_status == "unknown"


@pytest.mark.asyncio
async def test_http_status_adapter_connection_refused(client) -> None:
    entry = _entry(id="env_sensors", kind="environmental_sensor", adapter="http")
    adapter = HttpStatusAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(side_effect=httpx.ConnectError("refused"))
        result = await adapter.fetch(client)

    assert result.error is not None
    assert result.error.kind == "connection_refused"


# ---------------------------------------------------------------------------
# mock adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_adapter_unknown_for_unintegrated_equipment(client) -> None:
    entry = _entry(id="agilent_hplc", kind="hplc", adapter="mock", base_url=None)
    adapter = MockAdapter(entry)
    result = await adapter.fetch(client)
    assert result.error is None
    assert result.status.equipment_status == "unknown"
    assert "integrate_repo" in result.status.required_actions


@pytest.mark.asyncio
async def test_mock_adapter_synthesizes_sensor_metrics(client) -> None:
    entry = _entry(
        id="env_sensors_north_bench",
        kind="environmental_sensor",
        adapter="mock",
        base_url=None,
    )
    adapter = MockAdapter(entry)
    result = await adapter.fetch(client)
    assert result.error is None
    assert result.status.equipment_status == "dry_run"
    metric_keys = set(result.status.metrics.keys())
    assert metric_keys == {"temperature", "humidity", "o2", "voc"}
    assert result.status.metrics["temperature"].unit == "C"
    assert result.status.metrics["humidity"].unit == "%RH"
    assert result.status.metrics["o2"].unit == "%"
    assert result.status.metrics["voc"].unit == "ppb"
