"""Agent-facing workflow composition helpers.

The SDK owns the skill catalog and plan validator, so agent planners should
compose candidate workflows here instead of duplicating rule checks in prompts.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .interlocks import Violation
from .plan import Plan, PlanReport, Step, validate_plan
from .session import LabSession
from .skill_catalog import Skill


_TOKEN_RE = re.compile(r"[a-z0-9]+")


class AgentTask(BaseModel):
    """One requested lab operation for the agent planner to place in a workflow."""

    id: str
    goal: str
    args: dict[str, object] = Field(default_factory=dict)
    requires: list[str] = Field(default_factory=list)
    preferred_role: str | None = None
    confidence_hint: float | None = Field(default=None, ge=0.0, le=1.0)


class SkillCandidate(BaseModel):
    """Ranked skill match considered for a task."""

    role: str
    skill: str
    description: str
    available: bool
    reason: str | None = None
    score: float


class ExpertReviewRequest(BaseModel):
    """A task or plan finding that should be reviewed by a human expert."""

    task_id: str
    reason: str
    candidates: list[SkillCandidate] = Field(default_factory=list)
    proposed_step: Step | None = None
    violations: list[Violation] = Field(default_factory=list)


class ComposedWorkflow(BaseModel):
    """Planner output: accepted plan plus the expert review queue."""

    plan: Plan
    plan_report: PlanReport
    confidence: float
    accepted_tasks: list[str]
    skipped_tasks: list[str]
    review_requests: list[ExpertReviewRequest]


AgentRunState = Literal[
    "waiting_for_expert",
    "ready",
    "blocked",
    "running",
    "completed",
    "failed",
]


class ExpertDecision(BaseModel):
    """Human decision on a low-confidence or rule-blocked task."""

    task_id: str
    decision: Literal["approved", "rejected"]
    reviewer: str
    note: str | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExecutionStep(BaseModel):
    """Runtime execution record for one plan step."""

    step_id: str
    status: Literal["pending", "running", "completed", "failed", "skipped"]
    message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AgentRunEvent(BaseModel):
    """Audit event emitted by the agent runtime."""

    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event: str
    message: str


class AgentRun(BaseModel):
    """Stateful agent workflow run."""

    id: str
    objective: str
    binding: dict[str, str] = Field(default_factory=dict)
    tasks: list[AgentTask]
    workflow: ComposedWorkflow
    state: AgentRunState
    decisions: list[ExpertDecision] = Field(default_factory=list)
    execution: list[ExecutionStep] = Field(default_factory=list)
    events: list[AgentRunEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentRuntime:
    """In-memory agent workflow runtime.

    This owns the agent lifecycle around composition and expert review. It does
    not execute hardware commands directly; ``execute(..., dry_run=True)`` marks
    validated steps complete so callers can test the full runtime loop without
    bypassing the future SDK plan executor.
    """

    def __init__(self) -> None:
        self._runs: dict[str, AgentRun] = {}

    def create_run(
        self,
        *,
        objective: str,
        tasks: Sequence[AgentTask],
        skills: Iterable[Skill],
        session: LabSession,
        binding: Mapping[str, str] | None = None,
        confidence_threshold: float = 0.62,
    ) -> AgentRun:
        workflow = compose_workflow(
            tasks,
            skills,
            session,
            confidence_threshold=confidence_threshold,
        )
        run = AgentRun(
            id=f"agent_run_{uuid4().hex[:12]}",
            objective=objective,
            binding=dict(binding or session.binding),
            tasks=list(tasks),
            workflow=workflow,
            state=_state_for_workflow(workflow),
            events=[
                AgentRunEvent(
                    event="created",
                    message=(
                        f"Composed {len(workflow.plan.steps)} steps with "
                        f"{len(workflow.review_requests)} review requests."
                    ),
                )
            ],
        )
        run.execution = [
            ExecutionStep(step_id=s.id or f"step_{i}", status="pending")
            for i, s in enumerate(run.workflow.plan.steps)
        ]
        self._runs[run.id] = run
        return run

    def list_runs(self) -> list[AgentRun]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    def get_run(self, run_id: str) -> AgentRun | None:
        return self._runs.get(run_id)

    def approve_review(
        self,
        run_id: str,
        *,
        task_id: str,
        reviewer: str,
        note: str | None = None,
        session: LabSession,
    ) -> AgentRun:
        run = self._require_run(run_id)
        request = _find_review(run.workflow.review_requests, task_id)
        decision = ExpertDecision(
            task_id=task_id,
            decision="approved",
            reviewer=reviewer,
            note=note,
        )
        run.decisions.append(decision)
        if request.proposed_step is not None and not _plan_has_step(run.workflow.plan, task_id):
            run.workflow.plan.steps.append(request.proposed_step)
        run.workflow.review_requests = [
            r for r in run.workflow.review_requests if r.task_id != task_id
        ]
        run.workflow.plan_report = validate_plan(run.workflow.plan, session)
        run.state = _state_for_workflow(run.workflow)
        run.execution = [
            ExecutionStep(step_id=s.id or f"step_{i}", status="pending")
            for i, s in enumerate(run.workflow.plan.steps)
        ]
        _touch(
            run,
            "expert_approved",
            f"{reviewer} approved {task_id}: {note or 'no note'}",
        )
        return run

    def reject_review(
        self,
        run_id: str,
        *,
        task_id: str,
        reviewer: str,
        note: str | None = None,
    ) -> AgentRun:
        run = self._require_run(run_id)
        _find_review(run.workflow.review_requests, task_id)
        run.decisions.append(
            ExpertDecision(
                task_id=task_id,
                decision="rejected",
                reviewer=reviewer,
                note=note,
            )
        )
        run.workflow.review_requests = [
            r for r in run.workflow.review_requests if r.task_id != task_id
        ]
        run.state = _state_for_workflow(run.workflow)
        _touch(
            run,
            "expert_rejected",
            f"{reviewer} rejected {task_id}: {note or 'no note'}",
        )
        return run

    def execute(self, run_id: str, *, dry_run: bool = True) -> AgentRun:
        run = self._require_run(run_id)
        if run.workflow.review_requests:
            raise RuntimeError("run still has pending expert review requests")
        if run.workflow.plan_report.violations:
            raise RuntimeError("run is blocked by plan validation violations")
        if not dry_run:
            raise RuntimeError("hardware execution is not available until SDK execute_plan lands")

        run.state = "running"
        _touch(run, "execution_started", "Dry-run execution started.")
        completed: list[ExecutionStep] = []
        for step in run.execution:
            now = datetime.now(timezone.utc)
            completed.append(
                step.model_copy(
                    update={
                        "status": "completed",
                        "message": "Dry-run completed; no hardware command was sent.",
                        "started_at": now,
                        "finished_at": now,
                    }
                )
            )
        run.execution = completed
        run.state = "completed"
        _touch(run, "execution_completed", "Dry-run execution completed.")
        return run

    def _require_run(self, run_id: str) -> AgentRun:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run


def compose_workflow(
    tasks: Sequence[AgentTask],
    skills: Iterable[Skill],
    session: LabSession,
    *,
    confidence_threshold: float = 0.62,
    max_candidates: int = 3,
) -> ComposedWorkflow:
    """Compose a conservative :class:`Plan` from requested tasks.

    The planner ranks runtime lab skills against each task, emits only
    high-confidence matches into the executable plan, and queues ambiguous or
    low-confidence tasks for expert review. The accepted plan is then validated
    with the same offline rules used by workflow code, and blocking rule
    findings are also surfaced as expert review requests.
    """

    ranked_skills = list(skills)
    steps: list[Step] = []
    accepted_tasks: list[str] = []
    skipped_tasks: list[str] = []
    review_requests: list[ExpertReviewRequest] = []
    accepted_scores: list[float] = []

    for task in tasks:
        candidates = _rank_candidates(task, ranked_skills, limit=max_candidates)
        best = candidates[0] if candidates else None
        score = best.score if best is not None else 0.0
        if task.confidence_hint is not None:
            score = min(score, task.confidence_hint)

        proposed_step = (
            Step(
                id=task.id,
                role=best.role,
                skill=best.skill,
                args=task.args,
                requires=task.requires,
            )
            if best is not None
            else None
        )

        if best is None:
            skipped_tasks.append(task.id)
            review_requests.append(
                ExpertReviewRequest(
                    task_id=task.id,
                    reason="No lab skill matched this task.",
                )
            )
            continue

        if not best.available:
            skipped_tasks.append(task.id)
            review_requests.append(
                ExpertReviewRequest(
                    task_id=task.id,
                    reason=(
                        "Best matching skill is unavailable: "
                        f"{best.reason or 'unknown reason'}."
                    ),
                    candidates=candidates,
                    proposed_step=proposed_step,
                )
            )
            continue

        if score < confidence_threshold:
            skipped_tasks.append(task.id)
            review_requests.append(
                ExpertReviewRequest(
                    task_id=task.id,
                    reason=(
                        f"Planner confidence {score:.2f} is below "
                        f"threshold {confidence_threshold:.2f}."
                    ),
                    candidates=candidates,
                    proposed_step=proposed_step,
                )
            )
            continue

        if _is_ambiguous(candidates, score):
            skipped_tasks.append(task.id)
            review_requests.append(
                ExpertReviewRequest(
                    task_id=task.id,
                    reason="Multiple lab skills scored too closely for automatic selection.",
                    candidates=candidates,
                    proposed_step=proposed_step,
                )
            )
            continue

        assert proposed_step is not None
        steps.append(proposed_step)
        accepted_tasks.append(task.id)
        accepted_scores.append(score)

    plan = Plan(steps=steps)
    report = validate_plan(plan, session)
    review_requests.extend(_rule_review_requests(report))

    confidence = min(accepted_scores) if accepted_scores else 0.0
    if report.violations:
        confidence = min(confidence, 0.3)

    return ComposedWorkflow(
        plan=plan,
        plan_report=report,
        confidence=confidence,
        accepted_tasks=accepted_tasks,
        skipped_tasks=skipped_tasks,
        review_requests=review_requests,
    )


def _rank_candidates(
    task: AgentTask, skills: Sequence[Skill], *, limit: int
) -> list[SkillCandidate]:
    task_tokens = _tokens(task.goal)
    candidates: list[SkillCandidate] = []
    for skill in skills:
        skill_tokens = _tokens(
            " ".join(
                [
                    skill.name.replace(".", " "),
                    skill.role,
                    skill.kind,
                    skill.description,
                ]
            )
        )
        if not skill_tokens or not task_tokens:
            score = 0.0
        else:
            overlap = len(task_tokens & skill_tokens)
            score = overlap / len(task_tokens)
            if task.preferred_role == skill.role:
                score += 0.2
            if skill.name.replace(".", " ") in task.goal.lower():
                score += 0.25
            if not skill.available:
                score -= 0.15
        candidates.append(
            SkillCandidate(
                role=skill.role,
                skill=skill.name,
                description=skill.description,
                available=skill.available,
                reason=skill.reason,
                score=max(0.0, min(score, 1.0)),
            )
        )
    return sorted(candidates, key=lambda c: c.score, reverse=True)[:limit]


def _tokens(value: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(value.lower()) if len(t) > 2}


def _is_ambiguous(candidates: Sequence[SkillCandidate], best_score: float) -> bool:
    if len(candidates) < 2:
        return False
    runner_up = candidates[1]
    return runner_up.available and best_score - runner_up.score < 0.08


def _rule_review_requests(report: PlanReport) -> list[ExpertReviewRequest]:
    requests: list[ExpertReviewRequest] = []
    by_step: dict[str, list[Violation]] = {}
    for violation in report.violations:
        by_step.setdefault(violation.step_id, []).append(violation)
    for step_id, violations in by_step.items():
        requests.append(
            ExpertReviewRequest(
                task_id=step_id,
                reason="Plan validation rules blocked this step.",
                violations=violations,
            )
        )
    return requests


def _state_for_workflow(workflow: ComposedWorkflow) -> AgentRunState:
    if workflow.review_requests:
        return "waiting_for_expert"
    if workflow.plan_report.violations:
        return "blocked"
    return "ready"


def _find_review(
    review_requests: Sequence[ExpertReviewRequest], task_id: str
) -> ExpertReviewRequest:
    for request in review_requests:
        if request.task_id == task_id:
            return request
    raise KeyError(task_id)


def _plan_has_step(plan: Plan, step_id: str) -> bool:
    return any(step.id == step_id for step in plan.steps)


def _touch(run: AgentRun, event: str, message: str) -> None:
    run.updated_at = datetime.now(timezone.utc)
    run.events.append(AgentRunEvent(event=event, message=message))


__all__ = [
    "AgentTask",
    "AgentRun",
    "AgentRunEvent",
    "AgentRuntime",
    "ComposedWorkflow",
    "ExecutionStep",
    "ExpertDecision",
    "ExpertReviewRequest",
    "SkillCandidate",
    "compose_workflow",
]
