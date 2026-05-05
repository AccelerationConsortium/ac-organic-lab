"""Sync-wrapper tests for ``lab_skills.sync``.

The wrapper translates ``async with Lab.connect(...)`` into a plain ``with``
context, with status / probe / health / command available without ``await``.
We exercise the wrapper inside the async test loop via
``asyncio.to_thread`` because the wrapper owns its own private event loop
and would otherwise collide with pytest-asyncio's loop.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from lab_skills import (
    EquipmentBusy,
    EquipmentEntry,
    Registry,
)
from lab_skills.sync import Lab, SyncLabSession


def _entry() -> EquipmentEntry:
    return EquipmentEntry(
        id="sync_dev",
        name="Sync Device",
        platform="hte",
        kind="plate_sealer",
        adapter="http",
        base_url="http://sync.test:8000",
        poll_timeout_seconds=1.0,
    )


def _status_body(entry: EquipmentEntry, state: str = "ready") -> dict:
    return {
        "protocol_version": "1.0",
        "equipment_id": entry.id,
        "equipment_name": entry.name,
        "equipment_kind": entry.kind,
        "equipment_status": state,
        "device_time": "2026-04-29T22:50:01Z",
    }


def test_sync_lab_connect_status_round_trip() -> None:
    """``Lab.connect(...)`` works as a plain ``with`` context manager and
    exposes ``status()`` without ``await``.
    """

    entry = _entry()
    registry = Registry(equipment=[entry])

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(
            return_value=httpx.Response(200, json=_status_body(entry, "ready"))
        )
        with Lab.connect(registry=registry) as lab:
            assert isinstance(lab, SyncLabSession)
            client = lab.get(entry.id)
            envelope = client.status()
    assert envelope.equipment_status == "ready"
    assert envelope.equipment_id == entry.id


def test_sync_lab_command_round_trip() -> None:
    entry = _entry()
    registry = Registry(equipment=[entry])

    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/start").mock(
            return_value=httpx.Response(200, json={"ok": True, "message": "started"})
        )
        with Lab.connect(registry=registry) as lab:
            client = lab.get(entry.id)
            result = client.command(
                "/control/seal/start",
                {"temperature_c": 170, "seconds": 3.0},
            )
    assert result == {"ok": True, "message": "started"}


def test_sync_lab_command_busy_propagates_typed_exception() -> None:
    entry = _entry()
    registry = Registry(equipment=[entry])

    with respx.mock(base_url=entry.base_url) as router:
        router.post("/control/seal/start").mock(
            return_value=httpx.Response(409, json={"detail": "Busy"})
        )
        with Lab.connect(registry=registry) as lab:
            client = lab.get(entry.id)
            with pytest.raises(EquipmentBusy):
                client.command("/control/seal/start", {"temperature_c": 170})


def test_sync_lab_role_binding() -> None:
    entry = _entry()
    registry = Registry(equipment=[entry])

    with respx.mock(base_url=entry.base_url) as router:
        router.get("/status").mock(
            return_value=httpx.Response(200, json=_status_body(entry, "busy"))
        )
        with Lab.connect(registry=registry, binding={"sealer": entry.id}) as lab:
            assert lab.binding == {"sealer": entry.id}
            envelope = lab.role("sealer").status()
    assert envelope.equipment_status == "busy"


@pytest.mark.asyncio
async def test_sync_lab_runs_inside_async_test_loop() -> None:
    """The sync wrapper owns its own event loop, so it must work even when
    invoked from inside another loop's worker thread (e.g. a notebook cell
    spawning a thread for sync work). We simulate that with
    :func:`asyncio.to_thread`.
    """

    entry = _entry()
    registry = Registry(equipment=[entry])

    def _work() -> str:
        with respx.mock(base_url=entry.base_url) as router:
            router.get("/status").mock(
                return_value=httpx.Response(200, json=_status_body(entry, "ready"))
            )
            with Lab.connect(registry=registry) as lab:
                return lab.get(entry.id).status().equipment_status

    state = await asyncio.to_thread(_work)
    assert state == "ready"
