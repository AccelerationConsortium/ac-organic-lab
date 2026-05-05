"""``LabSession.skills()`` tests.

Cover the precedence rules from ``docs/SKILLS_CATALOG.md``:

* maintenance / disabled -> available=False with maintenance reason
* unreachable device     -> available=False with unreachable reason
* v1.1 ``allowed_actions`` (when present) overrides ``requires_states``
* v1.0 fallback: ``equipment_status in def.requires_states``
* concurrent fan-out across roles works
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from ac_organic_lab_skills import (
    EquipmentEntry,
    Lab,
    Registry,
    SKILL_REGISTRY,
    Skill,
)
from ac_organic_lab_skills.registry import Maintenance


def _entry(
    eid: str = "plateloc",
    *,
    kind: str = "plate_sealer",
    base_url: str | None = "http://plateloc.test:8000",
    enabled: bool = True,
    maintenance: Maintenance | None = None,
) -> EquipmentEntry:
    return EquipmentEntry(
        id=eid,
        name=eid.title(),
        platform="hte",
        kind=kind,  # type: ignore[arg-type]
        adapter="http",
        base_url=base_url,
        poll_timeout_seconds=1.0,
        enabled=enabled,
        maintenance=maintenance,
    )


def _status_body(
    entry: EquipmentEntry,
    state: str = "ready",
    *,
    extras: dict | None = None,
) -> dict:
    base = {
        "protocol_version": "1.0",
        "equipment_id": entry.id,
        "equipment_name": entry.name,
        "equipment_kind": entry.kind,
        "equipment_status": state,
        "device_time": "2026-04-29T22:50:01Z",
    }
    if extras:
        base.update(extras)
    return base


@pytest.mark.asyncio
async def test_skills_returns_full_catalog_for_ready_role() -> None:
    entry = _entry()
    registry = Registry(equipment=[entry])

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(
            return_value=httpx.Response(200, json=_status_body(entry, "ready"))
        )
        async with Lab.connect(
            registry=registry, binding={"sealer": entry.id}
        ) as lab:
            skills = await lab.skills()

    assert all(isinstance(s, Skill) for s in skills)
    expected_names = {d.name for d in SKILL_REGISTRY["plate_sealer"]}
    by_name = {s.name: s for s in skills if s.role == "sealer"}
    assert set(by_name) == expected_names

    # On a ready device: seal.start is available, seal.stop (busy-only) is not.
    assert by_name["seal.start"].available is True
    assert by_name["seal.start"].reason is None
    assert by_name["seal.stop"].available is False
    assert "busy" in (by_name["seal.stop"].reason or "")


@pytest.mark.asyncio
async def test_skills_busy_state_flips_seal_start_to_unavailable() -> None:
    entry = _entry()
    registry = Registry(equipment=[entry])

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(
            return_value=httpx.Response(200, json=_status_body(entry, "busy"))
        )
        async with Lab.connect(
            registry=registry, binding={"sealer": entry.id}
        ) as lab:
            skills = await lab.skills()

    by_name = {s.name: s for s in skills}
    assert by_name["seal.start"].available is False
    assert "ready" in (by_name["seal.start"].reason or "")
    assert by_name["seal.stop"].available is True


@pytest.mark.asyncio
async def test_skills_maintenance_role_marked_unavailable() -> None:
    sealer = _entry()
    press = _entry(
        "filtration_press",
        kind="press",
        base_url="http://press.test:8000",
        maintenance=Maintenance(
            reason="Awaiting replacement seal foil",
            until=date(2026, 6, 15),
            contact="alice@lab",
        ),
    )
    registry = Registry(equipment=[sealer, press])

    with respx.mock() as router:
        router.get("http://plateloc.test:8000/status").mock(
            return_value=httpx.Response(200, json=_status_body(sealer, "ready"))
        )
        # Press should NOT be polled because it's in maintenance; respx will
        # raise on any unexpected request.
        async with Lab.connect(
            registry=registry,
            binding={"sealer": sealer.id, "press": press.id},
        ) as lab:
            skills = await lab.skills()

    press_skills = [s for s in skills if s.role == "press"]
    assert press_skills, "press role should still appear in the catalog"
    for s in press_skills:
        assert s.available is False
        assert "maintenance" in (s.reason or "")
        assert "seal foil" in (s.reason or "")


@pytest.mark.asyncio
async def test_skills_disabled_role_marked_unavailable() -> None:
    """``enabled: false`` (no maintenance block) gets a different reason."""

    sealer = _entry(enabled=False)
    registry = Registry(equipment=[sealer])

    async with Lab.connect(
        registry=registry, binding={"sealer": sealer.id}
    ) as lab:
        skills = await lab.skills()

    assert skills, "skill list for disabled role should still be populated"
    for s in skills:
        assert s.available is False
        assert "disabled" in (s.reason or "")


@pytest.mark.asyncio
async def test_skills_unreachable_role_marked_unavailable() -> None:
    sealer = _entry()
    registry = Registry(equipment=[sealer])

    with respx.mock(base_url=sealer.base_url) as router:
        router.get("/status").mock(side_effect=httpx.TimeoutException("nope"))
        async with Lab.connect(
            registry=registry, binding={"sealer": sealer.id}
        ) as lab:
            skills = await lab.skills()

    by_name = {s.name: s for s in skills}
    assert by_name["seal.start"].available is False
    assert "unreachable" in (by_name["seal.start"].reason or "")


@pytest.mark.asyncio
async def test_skills_v11_allowed_actions_overrides_requires_states() -> None:
    """If the device declares ``allowed_actions`` (STATUS_SPEC v1.1), the SDK
    uses it as the source of truth even if the device's ``equipment_status``
    would normally satisfy ``requires_states``.

    Catches forward compatibility: v0.2 SDK + v1.1 device should agree.
    """

    sealer = _entry()
    registry = Registry(equipment=[sealer])

    # equipment_status='ready' would normally permit seal.start (per the
    # v1.0 fallback), but the device explicitly declares "stage.out is the
    # only thing you may do right now". The SDK must honour that.
    body = _status_body(sealer, "ready", extras={"allowed_actions": ["stage.out"]})

    with respx.mock(base_url=sealer.base_url) as router:
        router.get("/status").mock(return_value=httpx.Response(200, json=body))
        async with Lab.connect(
            registry=registry, binding={"sealer": sealer.id}
        ) as lab:
            skills = await lab.skills()

    by_name = {s.name: s for s in skills}
    assert by_name["seal.start"].available is False
    assert "does not currently allow" in (by_name["seal.start"].reason or "")
    assert by_name["stage.out"].available is True


@pytest.mark.asyncio
async def test_skills_concurrent_across_multiple_roles() -> None:
    """Two roles, both ready - both fetched concurrently and both contribute
    their full catalog to the result.
    """

    sealer = _entry("plateloc", kind="plate_sealer")
    hood = _entry(
        "fume_hood_actuator",
        kind="fume_hood",
        base_url="http://hood.test:8000",
    )
    registry = Registry(equipment=[sealer, hood])

    with respx.mock() as router:
        router.get("http://plateloc.test:8000/status").mock(
            return_value=httpx.Response(200, json=_status_body(sealer, "ready"))
        )
        router.get("http://hood.test:8000/status").mock(
            return_value=httpx.Response(200, json=_status_body(hood, "ready"))
        )
        async with Lab.connect(
            registry=registry,
            binding={"sealer": sealer.id, "hood": hood.id},
        ) as lab:
            skills = await lab.skills()

    by_role = {s.role for s in skills}
    assert by_role == {"sealer", "hood"}
    sealer_count = sum(1 for s in skills if s.role == "sealer")
    hood_count = sum(1 for s in skills if s.role == "hood")
    assert sealer_count == len(SKILL_REGISTRY["plate_sealer"])
    assert hood_count == len(SKILL_REGISTRY["fume_hood"])


@pytest.mark.asyncio
async def test_skills_unbound_session_returns_empty_list() -> None:
    """No binding -> no skills (and no errors)."""

    sealer = _entry()
    registry = Registry(equipment=[sealer])

    async with Lab.connect(registry=registry) as lab:
        skills = await lab.skills()

    assert skills == []


@pytest.mark.asyncio
async def test_skills_kind_with_no_registered_defs_returns_no_entries() -> None:
    """A role bound to ``kind=robot_arm`` (registered with empty defs)
    contributes nothing to the catalog. The role still works for ``status()``
    via ``role()``; it just has no invokable capabilities surfaced.
    """

    arm = _entry("xarm", kind="robot_arm", base_url="http://xarm.test:8000")
    registry = Registry(equipment=[arm])

    with respx.mock(base_url=arm.base_url) as router:
        # No /status request expected because there are no SkillDefs to
        # evaluate; respx would raise on an unexpected request.
        router  # noqa: B018 - keep the context manager alive
        async with Lab.connect(
            registry=registry, binding={"arm": arm.id}
        ) as lab:
            skills = await lab.skills()

    arm_skills = [s for s in skills if s.role == "arm"]
    assert arm_skills == []
