/**
 * Server-run assistant plans (UI_DESIGN §5.3b; api/app/assistant_plan_runs.py).
 *
 * The operator approves a plan card and presses Run; the dashboard server then
 * holds the device claim and sends the steps, so the tab may close. This
 * client starts a run, follows it, and offers the operator verbs. It never
 * sends a device command itself.
 *
 * Plain `fetch` rather than `@/lib/api`'s `fetchJson`, deliberately: the
 * bubble's tests mock `@/lib/api` wholesale (see lib/assistant-sessions.ts).
 */

export type ServerStepOutcome = "pending" | "running" | "ok" | "failed" | "skipped" | "unknown";
export type ServerRunStatus = "executing" | "paused" | "executed" | "failed" | "aborted" | "interrupted";

export interface ServerStepResult {
  index: number;
  action: string;
  outcome: ServerStepOutcome;
  status_code: number | null;
  message: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** A `read.*` step's full response; null when withheld from this viewer. */
  response?: unknown;
  response_withheld?: boolean;
}

export interface ServerRunSummary {
  plan_id: string;
  equipment_id: string;
  equipment_name: string;
  kind: string;
  status: ServerRunStatus;
  paused: boolean;
  approved_by: string;
  owner: string;
  started_at: string;
  finished_at: string | null;
  halt_reason: string | null;
  eln_project: string | null;
  notebook: { state: string; error?: string } | null;
  steps_total: number;
  steps_ok: number;
  current_step: number | null;
}

export interface ServerRun extends ServerRunSummary {
  step_hash: string;
  reason: string | null;
  steps: { action: string; passthrough_action: string; args: Record<string, unknown> }[];
  results: ServerStepResult[];
}

export const RUN_ACTIVE: ReadonlySet<ServerRunStatus> = new Set(["executing", "paused"]);

export class PlanRunApiError extends Error {
  readonly status: number;
  /** Machine code when the server sent one (e.g. `no_claim_protocol`). */
  readonly code: string | null;
  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    let code: string | null = null;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
      else if (body?.detail && typeof body.detail.message === "string") {
        detail = body.detail.message;
        code = typeof body.detail.code === "string" ? body.detail.code : null;
      }
    } catch {
      /* non-JSON error body — keep the status line */
    }
    throw new PlanRunApiError(res.status, detail, code);
  }
  return (await res.json()) as T;
}

const planPath = (planId: string, verb: string) =>
  `/api/assistant/plans/${encodeURIComponent(planId)}/${verb}`;

/** Start an approved plan on the server. The approver only. */
export function executePlan(planId: string, stepHash: string, elnProject: string | null): Promise<ServerRun> {
  return request<ServerRun>(planPath(planId, "execute"), {
    method: "POST",
    body: JSON.stringify({ step_hash: stepHash, eln_project: elnProject }),
  });
}

export function getPlanRun(planId: string): Promise<ServerRun> {
  return request<ServerRun>(planPath(planId, "run"));
}

export function listActivePlanRuns(): Promise<ServerRunSummary[]> {
  return request<ServerRunSummary[]>("/api/assistant/plan-runs?active=true");
}

export type PlanRunVerb = "pause" | "resume" | "stop";

/** Anyone signed in may pause or stop; only the approver resumes. */
export function planRunVerb(planId: string, verb: PlanRunVerb): Promise<ServerRunSummary> {
  return request<ServerRunSummary>(planPath(planId, verb), { method: "POST" });
}

/** ELN projects the signed-in user may file results into (the card's picker). */
export async function listElnProjects(): Promise<{ configured: boolean; projects: string[] }> {
  return request<{ configured: boolean; projects: string[] }>("/api/assistant/eln-projects");
}

/** The legacy browser runner, kept as a fallback (UI_DESIGN §5.3b). */
export function browserPlanRunnerForced(): boolean {
  return process.env.NEXT_PUBLIC_ASSISTANT_BROWSER_PLAN_RUNNER === "true";
}
