import asyncio
from types import SimpleNamespace

import pytest
from lab_skills import Lab, Plan, Step, execute_plan, validate_plan
from lab_skills.plan import ManualOutcome, ManualSpec
from lab_skills.registry import Registry, EquipmentEntry


def manual(**over):
    return Step(id="carry", kind="manual", manual={"instructions": "Carry the plate to the reviewed bench location", **over})


@pytest.mark.asyncio
async def test_manual_wait_blocks_next_step_and_preserves_explicit_outcomes(monkeypatch):
    import lab_skills.plan as mod
    waiting = asyncio.Event()
    response = asyncio.Event()
    seen = []
    async def provider(step):
        seen.append(step.id); waiting.set(); await response.wait()
        return ManualOutcome(outcome="failed", confirmed_by="chemist", request_id="req", note="plate damaged")
    async with Lab.connect(registry=Registry(equipment=[]), binding={}) as lab:
        plan = Plan(steps=[manual(), manual().model_copy(update={"id": "later"})])
        task = asyncio.create_task(execute_plan(plan, lab, owner="chemist", on_manual=provider))
        await asyncio.wait_for(waiting.wait(), 1)
        assert not task.done() and seen == ["carry"]
        response.set()
        report = await task
    assert not report.ok
    assert [s.status for s in report.steps] == ["failed", "skipped"]
    assert report.steps[0].response["confirmed_by"] == "chemist"


@pytest.mark.asyncio
async def test_manual_requires_provider_but_dry_run_never_prompts():
    async with Lab.connect(registry=Registry(equipment=[]), binding={}) as lab:
        with pytest.raises(ValueError, match="operator provider"):
            await execute_plan(Plan(steps=[manual()]), lab, owner="chemist")
        report = await execute_plan(Plan(steps=[manual()]), lab, owner="chemist", dry_run=True)
        assert report.ok and report.steps[0].status == "dry_run"


@pytest.mark.asyncio
@pytest.mark.parametrize("state,activity,position,claim_loss", [
    ("busy", "running", "out", False), ("ready", "unknown", "out", False),
    ("ready", "idle", "in", False), ("ready", "idle", "out", True),
    ("ready", "idle", "out", False),
])
async def test_claimed_access_checks_and_claim_loss(monkeypatch, state, activity, position, claim_loss):
    import lab_skills.plan as mod
    events = []
    class Claim:
        degraded = False
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): events.append("claim"); return self
        async def __aexit__(self, *a): events.append("release")
        def assert_alive(self):
            if claim_loss: raise RuntimeError("claim lost")
    class Client:
        equipment_id = "reader"
        async def status(self):
            return SimpleNamespace(equipment_status=state, activity=activity,
                model_dump=lambda **kw: {"details": {"stage": {"position": position}}})
    monkeypatch.setattr(mod, "ClaimManager", Claim)
    registry = Registry(equipment=[EquipmentEntry(id="reader", name="Reader", kind="plate_reader", adapter="http", base_url="http://unused.test", protocol="1.2")])
    session = SimpleNamespace(registry=registry, binding={"reader": "reader"}, role=lambda _: Client())
    async def provider(step):
        events.append("prompt")
        if claim_loss:
            await asyncio.sleep(5)
        return ManualOutcome(outcome="completed", confirmed_by="chemist", request_id="req")
    step = manual(access_roles=["reader"], access_checks={"reader": {"details.stage.position": "out"}})
    report = await execute_plan(Plan(steps=[step]), session, owner="chemist", on_manual=provider)
    assert events[0] == "claim" and events[-1] == "release"
    good = state == "ready" and activity == "idle" and position == "out"
    assert ("prompt" in events) is good
    assert report.ok is (good and not claim_loss)


@pytest.mark.parametrize("spec", [
    {"instructions": "        "},
    {"instructions": "Move this plate", "access_roles": ["reader"]},
    {"instructions": "Move this plate", "unexpected": True},
])
def test_invalid_instructions_are_refused(spec):
    with pytest.raises(ValueError): ManualSpec.model_validate(spec)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed"])
async def test_device_command_waits_for_human_and_only_follows_success(outcome):
    import httpx
    import respx
    from test_execute_plan import BASE, _registry, _mock_status, _mock_claim_lifecycle
    waiting, acknowledged = asyncio.Event(), asyncio.Event()
    async def provider(step):
        waiting.set()
        await acknowledged.wait()
        return ManualOutcome(outcome=outcome, confirmed_by="chemist", request_id="req")
    with respx.mock:
        _mock_status(["stage.in"])
        _mock_claim_lifecycle()
        command = respx.post(f"{BASE}/control/stage/in").mock(
            return_value=httpx.Response(200, json={"ok": True}))
        async with Lab.connect(registry=_registry(), binding={"sealer": "plateloc"}) as lab:
            task = asyncio.create_task(execute_plan(Plan(steps=[manual(),
                Step(id="next", role="sealer", skill="stage.in")]), lab,
                owner="chemist", on_manual=provider))
            await asyncio.wait_for(waiting.wait(), 1)
            assert command.call_count == 0 and not task.done()
            acknowledged.set()
            report = await task
        assert command.call_count == (1 if outcome == "completed" else 0)
        assert report.ok is (outcome == "completed")
