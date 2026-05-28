export type AgentTask = {
  id: string;
  goal: string;
  args?: Record<string, unknown>;
  requires?: string[];
  preferred_role?: string | null;
  confidence_hint?: number | null;
};

export type AgentRunState =
  | "waiting_for_expert"
  | "ready"
  | "blocked"
  | "running"
  | "completed"
  | "failed";

export type AgentStep = {
  role: string;
  skill: string;
  args: Record<string, unknown>;
  id: string | null;
  requires: string[];
  index?: number | null;
};

export type ExpertReviewRequest = {
  task_id: string;
  reason: string;
  proposed_step?: AgentStep | null;
  violations: Array<{ code: string; message: string; severity: string }>;
};

export type AgentRun = {
  id: string;
  objective: string;
  binding: Record<string, string>;
  state: AgentRunState;
  tasks: AgentTask[];
  workflow: {
    plan: { steps: AgentStep[] };
    confidence: number;
    accepted_tasks: string[];
    skipped_tasks: string[];
    review_requests: ExpertReviewRequest[];
    plan_report: {
      ok: boolean;
      steps: Array<{
        step_id: string;
        step_index: number;
        role: string;
        skill: string;
        ok: boolean;
      }>;
    };
  };
  execution: Array<{ step_id: string; status: string; message?: string | null }>;
};

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `${response.status} ${response.statusText}`;
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function createAgentRun(input: {
  objective: string;
  tasks: AgentTask[];
  binding?: Record<string, string>;
  confidence_threshold?: number;
}): Promise<AgentRun> {
  return fetchJson<AgentRun>("/api/agent/runs", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function approveAgentReview(
  runId: string,
  taskId: string,
  reviewer: string,
  note?: string,
): Promise<AgentRun> {
  return fetchJson<AgentRun>(
    `/api/agent/runs/${encodeURIComponent(runId)}/reviews/${encodeURIComponent(taskId)}/approve`,
    {
      method: "POST",
      body: JSON.stringify({ reviewer, note }),
    },
  );
}

export async function rejectAgentReview(
  runId: string,
  taskId: string,
  reviewer: string,
  note?: string,
): Promise<AgentRun> {
  return fetchJson<AgentRun>(
    `/api/agent/runs/${encodeURIComponent(runId)}/reviews/${encodeURIComponent(taskId)}/reject`,
    {
      method: "POST",
      body: JSON.stringify({ reviewer, note }),
    },
  );
}

export async function executeAgentRun(runId: string): Promise<AgentRun> {
  return fetchJson<AgentRun>(`/api/agent/runs/${encodeURIComponent(runId)}/execute`, {
    method: "POST",
    body: JSON.stringify({ dry_run: true }),
  });
}
