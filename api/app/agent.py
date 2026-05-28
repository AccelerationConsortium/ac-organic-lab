"""Agent runtime API.

This router exposes the SDK's agent workflow runtime to the dashboard:
create a run, inspect expert review requests, record expert decisions, and
dry-run execute an approved plan.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from lab_skills import AgentRun, AgentRuntime, AgentTask, LabSession, Registry


class AgentRunCreateRequest(BaseModel):
    objective: str
    tasks: list[AgentTask]
    binding: dict[str, str] | None = None
    confidence_threshold: float = Field(default=0.62, ge=0.0, le=1.0)


class ExpertReviewDecisionRequest(BaseModel):
    reviewer: str
    note: str | None = None


class AgentExecuteRequest(BaseModel):
    dry_run: bool = True


def build_agent_router() -> APIRouter:
    router = APIRouter(prefix="/api/agent", tags=["agent"])

    @router.get("/runs", response_model=list[AgentRun])
    async def list_runs(request: Request) -> list[AgentRun]:
        return _runtime(request).list_runs()

    @router.post("/runs", response_model=AgentRun)
    async def create_run(
        payload: AgentRunCreateRequest,
        request: Request,
    ) -> AgentRun:
        registry = _registry(request)
        binding = payload.binding or _default_binding(registry)
        session = LabSession(registry=registry, binding=binding)

        state_skills = getattr(request.app.state, "agent_skills", None)
        if state_skills is not None:
            skills = state_skills
        else:
            async with session:
                skills = await session.skills()

        try:
            return _runtime(request).create_run(
                objective=payload.objective,
                tasks=payload.tasks,
                skills=skills,
                session=session,
                binding=binding,
                confidence_threshold=payload.confidence_threshold,
            )
        except Exception as exc:  # noqa: BLE001 - surface planner failure as API detail
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/runs/{run_id}", response_model=AgentRun)
    async def get_run(run_id: str, request: Request) -> AgentRun:
        run = _runtime(request).get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Unknown agent run: {run_id}")
        return run

    @router.post("/runs/{run_id}/reviews/{task_id}/approve", response_model=AgentRun)
    async def approve_review(
        run_id: str,
        task_id: str,
        payload: ExpertReviewDecisionRequest,
        request: Request,
    ) -> AgentRun:
        run = _require_run(request, run_id)
        session = LabSession(registry=_registry(request), binding=run.binding)
        try:
            return _runtime(request).approve_review(
                run_id,
                task_id=task_id,
                reviewer=payload.reviewer,
                note=payload.note,
                session=session,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown review task: {task_id}") from exc

    @router.post("/runs/{run_id}/reviews/{task_id}/reject", response_model=AgentRun)
    async def reject_review(
        run_id: str,
        task_id: str,
        payload: ExpertReviewDecisionRequest,
        request: Request,
    ) -> AgentRun:
        _require_run(request, run_id)
        try:
            return _runtime(request).reject_review(
                run_id,
                task_id=task_id,
                reviewer=payload.reviewer,
                note=payload.note,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown review task: {task_id}") from exc

    @router.post("/runs/{run_id}/execute", response_model=AgentRun)
    async def execute_run(
        run_id: str,
        payload: AgentExecuteRequest,
        request: Request,
    ) -> AgentRun:
        _require_run(request, run_id)
        try:
            return _runtime(request).execute(run_id, dry_run=payload.dry_run)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router


def _runtime(request: Request) -> AgentRuntime:
    runtime = getattr(request.app.state, "agent_runtime", None)
    if runtime is None:
        runtime = AgentRuntime()
        request.app.state.agent_runtime = runtime
    return runtime


def _registry(request: Request) -> Registry:
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Registry not initialized")
    return registry


def _require_run(request: Request, run_id: str) -> AgentRun:
    run = _runtime(request).get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent run: {run_id}")
    return run


def _default_binding(registry: Registry) -> dict[str, str]:
    return {
        entry.id: entry.id
        for entry in registry.equipment
        if entry.kind not in ("environmental_sensor", "camera")
    }


__all__ = [
    "AgentExecuteRequest",
    "AgentRunCreateRequest",
    "ExpertReviewDecisionRequest",
    "build_agent_router",
]
