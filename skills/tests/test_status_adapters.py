"""Adapter behaviour tests using captured fixture payloads.

We use `respx` to mock outbound HTTP and feed each adapter a realistic body,
then assert the normalized envelope. These act as the "snapshot tests against
captured device payloads" called for in the plan.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from lab_skills.status_adapters import (
    HttpStatusAdapter,
    LegacyDoseEveryWellAdapter,
    LegacyFilterEveryWellAdapter,
    LegacyFumeHoodActuatorAdapter,
    MockAdapter,
)
from lab_skills.registry import EquipmentEntry


def _entry(**overrides) -> EquipmentEntry:
    base = dict(
        id="test_equipment",
        name="Test Equipment",
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
# dose_every_well — spec-compliant (production: adapter: http)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doser_requires_init(load_fixture, client) -> None:
    """dose_every_well now emits spec v1.0 EquipmentStatus; routed via HttpStatusAdapter."""
    http_status, body = load_fixture("dose_every_well_requires_init")
    entry = _entry(id="dose_every_well", kind="solid_doser", adapter="http")
    adapter = HttpStatusAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(http_status, json=body))
        result = await adapter.fetch(client)

    assert result.error is None
    assert result.status.equipment_status == "requires_init"
    assert result.status.required_actions == ["startup"]
    assert result.status.components["gantry"].connected is False
    assert result.status.components["solid_doser"].connected is False


@pytest.mark.asyncio
async def test_doser_ready(load_fixture, client) -> None:
    """dose_every_well now emits spec v1.0 EquipmentStatus; routed via HttpStatusAdapter."""
    http_status, body = load_fixture("dose_every_well_ready")
    entry = _entry(id="dose_every_well", kind="solid_doser", adapter="http")
    adapter = HttpStatusAdapter(entry)

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
# dose_every_well — legacy adapter (pre-migration behaviour, kept for rollback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_doser_requires_init(load_fixture, client) -> None:
    """LegacyDoseEveryWellAdapter maps HTTP 400 not-initialized to requires_init."""
    http_status, body = load_fixture("dose_every_well_legacy_requires_init")
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
async def test_legacy_doser_ready(load_fixture, client) -> None:
    """LegacyDoseEveryWellAdapter translates the pre-migration flat JSON body."""
    http_status, body = load_fixture("dose_every_well_legacy_ready")
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


@pytest.mark.asyncio
async def test_fume_hood_requires_init_when_position_unknown(load_fixture, client) -> None:
    http_status, body = load_fixture("fume_hood_actuator_requires_init")
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

    assert result.status.equipment_status == "requires_init"
    assert "sash_position" not in result.status.metrics
    assert "target_position" not in result.status.metrics


# ---------------------------------------------------------------------------
# xarm_translocation
#
# The repo now conforms to STATUS_SPEC v1.0 (see ``xarm-translocation``); the
# fixtures below capture the spec ``EquipmentStatus`` envelope exactly as the
# device emits it. Routed through the standard ``HttpStatusAdapter`` (no
# bespoke translator) by ``equipment.yaml``: ``adapter: http``.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xarm_disconnected(load_fixture, client) -> None:
    http_status, body = load_fixture("xarm_disconnected")
    entry = _entry(
        id="xarm_translocation",
        kind="robot_arm",
        adapter="http",
        do_not_call_connect=True,
    )
    adapter = HttpStatusAdapter(entry)

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
    entry = _entry(
        id="xarm_translocation",
        kind="robot_arm",
        adapter="http",
        do_not_call_connect=True,
    )
    adapter = HttpStatusAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(http_status, json=body))
        result = await adapter.fetch(client)

    assert result.error is None
    assert result.status.equipment_status == "ready"
    assert result.status.components["arm"].connected
    assert result.status.components["arm"].state == "enabled"
    assert result.status.metrics["track_position"].unit == "mm"
    assert result.status.details["model_name"] == "xArm5"


@pytest.mark.asyncio
async def test_xarm_busy(load_fixture, client) -> None:
    http_status, body = load_fixture("xarm_busy")
    entry = _entry(
        id="xarm_translocation",
        kind="robot_arm",
        adapter="http",
        do_not_call_connect=True,
    )
    adapter = HttpStatusAdapter(entry)

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(http_status, json=body))
        result = await adapter.fetch(client)

    assert result.error is None
    assert result.status.equipment_status == "busy"
    assert result.status.metrics["track_position"].value == 237.0


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
    # Synthetic values, but a real zone's *shape*: the keys and units mirror
    # `sense-every-zone`'s `_METRIC_MAP` for a SEN55 + PiSugar basic node, so
    # mock and real zones take the same path through every reader.
    assert result.status.equipment_status == "dry_run"
    assert result.status.protocol_version == "1.2"
    metric_keys = set(result.status.metrics.keys())
    assert metric_keys == {
        "temperature", "humidity", "voc", "nox",
        "pm1", "pm25", "pm4", "pm10",
        "battery", "battery_voltage",
    }
    assert result.status.metrics["temperature"].unit == "°C"
    assert result.status.metrics["humidity"].unit == "%RH"
    # Sensirion VOC/NOx are unitless indices, not ppb concentrations.
    assert result.status.metrics["voc"].unit == "index"
    assert result.status.metrics["pm25"].unit == "µg/m³"
    assert result.status.metrics["battery"].unit == "%"
    # No Alphasense cells on a basic zone node — never invent gas channels.
    for absent in ("co", "o2", "h2"):
        assert absent not in metric_keys
    # Components mirror the device's naming (sen55_<zone> / ups_<zone>).
    assert set(result.status.components) == {
        "sen55_sensors_north_bench", "ups_sensors_north_bench",
    }
    # §2.3: dry_run permits any activity, but a quiet sensor is idle.
    assert result.status.activity == "idle"
