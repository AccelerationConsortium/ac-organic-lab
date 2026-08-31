"""Tests for the optional custody interlock factory.

What these pin is the custody discipline the rest of the system shares: a
ledger that names a *different* place is a blocking contradiction, while a
ledger that cannot answer — or has never heard of the container — is only a
warning, because not knowing where a plate is is not the same as knowing it is
in the wrong place. `strict=True` is the caller's choice to stop rather than
proceed unverified; it never turns absence into contradiction.

The lookup is a plain async callable, so nothing here (and nothing in `skills/`)
imports the dashboard: the fakes below answer in the shape
`CustodyRecorder.current_location` returns.
"""

from __future__ import annotations

import pytest

from lab_skills import (
    LabSession,
    Plan,
    Step,
    clear_interlocks,
    register_interlock,
)
from lab_skills.interlocks import (
    registered_interlocks,
    run_interlocks,
    run_interlocks_async,
)
from lab_skills.interlocks_custody import require_plate_at
from lab_skills.registry import EquipmentEntry, Registry

HID = "PLT-0042"
NEST = "torry_pines_shaker/nest"


@pytest.fixture(autouse=True)
def _reset_interlocks():
    clear_interlocks()
    yield
    clear_interlocks()


def _session() -> LabSession:
    entry = EquipmentEntry(
        id="torry_pines_shaker",
        name="Torrey Pines SC25XR",
        kind="shaker",
        adapter="http",
        base_url="http://shaker.local:8030",
        protocol="1.2",
        status_path="/status",
    )
    return LabSession(
        registry=Registry(equipment=[entry]), binding={"shaker": "torry_pines_shaker"}
    )


def _plan() -> Plan:
    return Plan(
        steps=[
            Step(id="shake_start", role="shaker", skill="shake.start",
                 args={"speed_level": 5, "seconds": 30}),
            Step(id="shake_stop", role="shaker", skill="shake.stop", args={}),
        ]
    )


def _answers(*rows):
    """An async lookup returning the given ledger rows in order, recording the
    hids it was asked about."""
    asked: list[str] = []
    queue = list(rows)

    async def lookup(hid: str) -> dict:
        asked.append(hid)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    lookup.asked = asked  # type: ignore[attr-defined]
    return lookup


def _at(name: str | None) -> dict:
    return {"found": True, "hid": HID, "container_id": "c1", "location_name": name}


async def _check(interlock, step_index: int = 0):
    plan = _plan()
    step = plan.steps[step_index].with_index(step_index)
    return await interlock(plan, step, _session()) or []


# ── the verdict table ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_ledger_agreeing_is_the_only_silent_outcome():
    ilk = require_plate_at(HID, NEST, _answers(_at(NEST)))
    assert await _check(ilk) == []


@pytest.mark.asyncio
async def test_a_different_place_blocks_and_names_both():
    """The contradiction case: the plan is about to shake a nest the ledger says
    is empty because the plate is still in the gripper. Critical regardless of
    `strict` — there is nothing unverified about it."""
    ilk = require_plate_at(HID, NEST, _answers(_at("xarm_translocation/gripper")))
    (v,) = await _check(ilk)
    assert v.code == "plate_not_at_location" and v.severity == "critical"
    assert NEST in v.message and "xarm_translocation/gripper" in v.message
    assert v.step_id == "shake_start" and v.step_index == 0
    assert v.interlock_name == f"require_plate_at[{HID}@{NEST}]"
    assert "nothing auto-corrects" in (v.actionable or "")
    # a placed-nowhere container contradicts a rule that names a place, too
    (v2,) = await _check(require_plate_at(HID, NEST, _answers(_at(None))))
    assert v2.code == "plate_not_at_location"


@pytest.mark.asyncio
async def test_absence_warns_where_contradiction_blocks():
    """Two different kinds of "we do not know", both non-blocking by default:
    the ledger has no such container, and the store could not answer. Neither is
    evidence that the plate is in the wrong place."""
    unknown = await _check(require_plate_at(HID, NEST, _answers({"found": False, "hid": HID})))
    assert [(v.code, v.severity) for v in unknown] == [("plate_unknown_to_ledger", "warning")]

    unanswered = await _check(require_plate_at(
        HID, NEST, _answers({"found": None, "hid": HID, "error": "connection refused"})))
    assert [(v.code, v.severity) for v in unanswered] == [("custody_lookup_failed", "warning")]
    assert "connection refused" in unanswered[0].message


@pytest.mark.asyncio
async def test_strict_stops_rather_than_proceed_unverified():
    """`strict=True` promotes only the two "could not verify" findings. It is a
    posture ("I would rather stop than run unverified"), not a reclassification
    of what the ledger said — the same trade the executor's CUSTODY_STRICT
    makes."""
    unknown = await _check(require_plate_at(
        HID, NEST, _answers({"found": False, "hid": HID}), strict=True))
    assert [(v.code, v.severity) for v in unknown] == [("plate_unknown_to_ledger", "error")]
    unanswered = await _check(require_plate_at(
        HID, NEST, _answers({"found": None, "hid": HID}), strict=True))
    assert [(v.code, v.severity) for v in unanswered] == [("custody_lookup_failed", "error")]
    # …and the contradiction is critical either way, so strict changes nothing
    blocked = await _check(require_plate_at(
        HID, NEST, _answers(_at("bench/hte_staging")), strict=True))
    assert blocked[0].severity == "critical"


@pytest.mark.asyncio
async def test_a_raising_lookup_is_a_finding_not_a_crash():
    """`run_interlocks_async` would turn this into a critical `interlock_error`,
    which is the right report for a *buggy rule* and the wrong one for a record
    layer that is simply down — the caller's `strict` should decide, not the
    runner's backstop."""

    async def boom(hid: str) -> dict:
        raise ConnectionError("record layer unreachable")

    (v,) = await _check(require_plate_at(HID, NEST, boom))
    assert v.code == "custody_lookup_failed" and v.severity == "warning"
    assert "ConnectionError" in v.message and "unreachable" in v.message


# ── narrowing ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_applies_to_narrows_the_rule_and_skips_the_lookup_entirely():
    """The lookup is I/O and runs before *every* step of the plan, so a rule
    that does not apply must not pay for it."""
    lookup = _answers(_at("bench/hte_staging"))
    ilk = require_plate_at(HID, NEST, lookup, applies_to={"shake_start"})
    assert len(await _check(ilk, 0)) == 1
    assert await _check(ilk, 1) == []
    assert lookup.asked == [HID]           # step 2 never reached the ledger


@pytest.mark.asyncio
async def test_applies_to_takes_a_predicate_over_the_step():
    ilk = require_plate_at(HID, NEST, _answers(_at("bench/hte_staging")),
                           applies_to=lambda step: step.skill.startswith("shake."))
    assert len(await _check(ilk, 0)) == 1
    assert len(await _check(ilk, 1)) == 1
    never = require_plate_at(HID, NEST, _answers(_at("bench/hte_staging")),
                             applies_to=lambda step: False)
    assert await _check(never, 0) == []


# ── registration ────────────────────────────────────────────────────────────


def test_one_rule_per_plate_does_not_replace_the_last():
    """`register_interlock` keys on the function's name and re-registration is
    deliberately a replacement, so two closures from the same factory must not
    share one. A lab checking three plates would otherwise silently check the
    third only."""
    a = require_plate_at("PLT-1", "ot2_hte/slot_2", _answers(_at(None)))
    b = require_plate_at("PLT-2", NEST, _answers(_at(None)))
    register_interlock(a)
    register_interlock(b)
    names = registered_interlocks()
    assert "require_plate_at[PLT-1@ot2_hte/slot_2]" in names
    assert f"require_plate_at[PLT-2@{NEST}]" in names
    register_interlock(require_plate_at("PLT-3", NEST, _answers(_at(None)),
                                        name="reaction_plate_is_on_the_shaker"))
    assert "reaction_plate_is_on_the_shaker" in registered_interlocks()


def test_the_factory_registers_nothing_on_its_own():
    """The registry is process-wide and outlives any one run; a factory that
    registered on your behalf would accumulate closures pinned to plates that
    left the lab hours ago."""
    before = set(registered_interlocks())
    require_plate_at(HID, NEST, _answers(_at(NEST)))
    assert set(registered_interlocks()) == before


@pytest.mark.asyncio
async def test_it_runs_where_execute_plan_runs_it():
    """The interlock is async, so it is evaluated by `run_interlocks_async` —
    the runner `execute_plan` calls immediately before each step — and not by
    the offline sync `run_interlocks` inside `validate_plan`. That is the point:
    the ledger is live state, and asking about it at validation time would only
    answer for a lab that has since moved on."""
    ilk = require_plate_at(HID, NEST, _answers(_at("xarm_translocation/gripper")),
                           applies_to={"shake_start"})
    register_interlock(ilk)
    plan, session = _plan(), _session()
    first, second = plan.steps[0].with_index(0), plan.steps[1].with_index(1)

    def mine(found):
        return [v.code for v in found if v.interlock_name == ilk.__name__]

    assert mine(await run_interlocks_async(plan, first, session)) == ["plate_not_at_location"]
    assert mine(await run_interlocks_async(plan, second, session)) == []
    # …and not by the offline runner, which skips async rules by design
    assert mine(run_interlocks(plan, first, session)) == []
