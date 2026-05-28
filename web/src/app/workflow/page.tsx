"use client";

import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { useCallback, useMemo, useState } from "react";
import {
  approveAgentReview,
  createAgentRun,
  executeAgentRun,
  rejectAgentReview,
  type AgentRun,
} from "@/lib/agent-api";

type ReviewState = "accepted" | "review" | "blocked";

type WorkflowStep = {
  id: string;
  label: string;
  role: string;
  skill: string;
  confidence: number;
  review: ReviewState;
  expertNote?: string;
  args?: Record<string, unknown>;
  requires?: string[];
};

type WorkflowNodeData = WorkflowStep & {
  [key: string]: unknown;
};
type WorkflowNode = Node<WorkflowNodeData, "workflowStep">;

const SAMPLE_STEPS: WorkflowStep[] = [
  {
    id: "dose",
    label: "Dose reagents",
    role: "doser",
    skill: "dose.every_well",
    confidence: 0.88,
    review: "accepted",
    args: { volume_ul: 40 },
  },
  {
    id: "seal",
    label: "Seal plate",
    role: "sealer",
    skill: "seal.start",
    confidence: 0.81,
    review: "accepted",
    args: { temperature_c: 170, seconds: 3 },
    requires: ["dose"],
  },
  {
    id: "shake",
    label: "Mix reaction plate",
    role: "shaker",
    skill: "shake.start",
    confidence: 0.74,
    review: "accepted",
    args: { rpm: 850, seconds: 120 },
    requires: ["seal"],
  },
  {
    id: "inspect",
    label: "Judge crystal morphology",
    role: "expert",
    skill: "expert.review",
    confidence: 0.42,
    review: "review",
    expertNote: "Planner confidence is below threshold; ask a chemist before execution.",
    requires: ["shake"],
  },
];

const SAMPLE_AGENT_TASKS = [
  {
    id: "seal_plate",
    goal: "seal",
    args: { temperature_c: 170, seconds: 3.0 },
    confidence_hint: 0.5,
  },
];

function stepToNode(step: WorkflowStep, index: number): WorkflowNode {
  return {
    id: step.id,
    type: "workflowStep",
    position: { x: (index % 2) * 360, y: Math.floor(index / 2) * 210 },
    data: step,
  };
}

function stepsToEdges(steps: WorkflowStep[]): Edge[] {
  return steps.flatMap((step) =>
    (step.requires ?? []).map((source) => ({
      id: `${source}-${step.id}`,
      source,
      target: step.id,
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed },
      className: step.review === "blocked" ? "stroke-rose-500" : "stroke-slate-400",
    })),
  );
}

function runToSteps(run: AgentRun): WorkflowStep[] {
  const reviewByTask = new Map(
    run.workflow.review_requests.map((request) => [request.task_id, request]),
  );
  const planSteps = run.workflow.plan.steps.map((step) => {
    const review = reviewByTask.get(step.id ?? "");
    return {
      id: step.id ?? `${step.role}-${step.skill}`,
      label: step.id ?? step.skill,
      role: step.role,
      skill: step.skill,
      confidence: run.workflow.confidence || 0.7,
      review: review ? ("review" as const) : ("accepted" as const),
      expertNote: review?.reason,
      args: step.args,
      requires: step.requires,
    };
  });
  const pendingReviews = run.workflow.review_requests
    .filter((request) => !planSteps.some((step) => step.id === request.task_id))
    .map((request, index) => ({
      id: request.task_id,
      label: request.task_id,
      role: request.proposed_step?.role ?? "expert",
      skill: request.proposed_step?.skill ?? "expert.review",
      confidence: 0.4,
      review: "review" as const,
      expertNote: request.reason,
      args: request.proposed_step?.args,
      requires: request.proposed_step?.requires ?? (index > 0 ? [planSteps[index - 1]?.id] : []),
    }));
  return [...planSteps, ...pendingReviews];
}

function confidenceLabel(confidence: number) {
  return `${Math.round(confidence * 100)}%`;
}

function WorkflowStepNode({ data, selected }: NodeProps<WorkflowNode>) {
  const review = data.review as ReviewState;
  const confidence = Number(data.confidence);
  const tone =
    review === "blocked"
      ? "border-rose-400 bg-rose-50 text-rose-950 dark:border-rose-500/70 dark:bg-rose-950/40 dark:text-rose-100"
      : review === "review"
        ? "border-amber-400 bg-amber-50 text-amber-950 dark:border-amber-400/70 dark:bg-amber-950/40 dark:text-amber-100"
        : "border-emerald-400 bg-emerald-50 text-emerald-950 dark:border-emerald-400/70 dark:bg-emerald-950/30 dark:text-emerald-100";

  return (
    <div
      className={`w-[280px] rounded-lg border-2 ${tone} ${
        selected ? "ring-2 ring-sky-400 ring-offset-2 dark:ring-offset-slate-950" : ""
      }`}
    >
      <Handle type="target" position={Position.Top} className="!bg-slate-500" />
      <div className="border-b border-current/15 px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">{String(data.label)}</p>
            <p className="mt-1 truncate font-mono text-[11px] opacity-75">
              {String(data.role)} · {String(data.skill)}
            </p>
          </div>
          <span className="shrink-0 rounded-full border border-current/20 px-2 py-1 font-mono text-[11px]">
            {confidenceLabel(confidence)}
          </span>
        </div>
      </div>
      <div className="space-y-2 px-4 py-3 text-xs">
        <div className="flex items-center justify-between gap-3">
          <span className="uppercase tracking-wide opacity-60">Gate</span>
          <span className="font-medium">
            {review === "review" ? "Expert review" : review === "blocked" ? "Blocked" : "Ready"}
          </span>
        </div>
        {data.expertNote ? (
          <p className="leading-5 opacity-80">{String(data.expertNote)}</p>
        ) : null}
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-slate-500" />
    </div>
  );
}

const nodeTypes = { workflowStep: WorkflowStepNode };

export default function WorkflowPage() {
  const [workflowName, setWorkflowName] = useState("Agent composed workflow");
  const [fileError, setFileError] = useState<string | null>(null);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [agentRun, setAgentRun] = useState<AgentRun | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(SAMPLE_STEPS[0].id);
  const [nodes, setNodes, onNodesChange] = useNodesState<WorkflowNode>(
    SAMPLE_STEPS.map(stepToNode),
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState(stepsToEdges(SAMPLE_STEPS));

  const selected = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId)?.data,
    [nodes, selectedNodeId],
  );
  const reviewCount = nodes.filter((node) => node.data.review === "review").length;
  const acceptedCount = nodes.filter((node) => node.data.review === "accepted").length;
  const lowestConfidence = nodes.length
    ? Math.min(...nodes.map((node) => Number(node.data.confidence)))
    : 0;

  const onConnect = useCallback(
    (connection: Connection) =>
      setEdges((current) =>
        addEdge(
          {
            ...connection,
            type: "smoothstep",
            markerEnd: { type: MarkerType.ArrowClosed },
          },
          current,
        ),
      ),
    [setEdges],
  );

  const onFile = useCallback(
    async (file: File | null) => {
      if (!file) return;
      try {
        const text = await file.text();
        const parsed = JSON.parse(text) as {
          workflow_name?: string;
          steps?: WorkflowStep[];
          phases?: Array<{
            phase_name: string;
            description?: string;
            requires?: string[];
            confidence?: number;
          }>;
        };
        const nextSteps =
          parsed.steps ??
          (parsed.phases ?? []).map((phase) => ({
            id: phase.phase_name,
            label: phase.description ?? phase.phase_name,
            role: "phase",
            skill: phase.phase_name,
            confidence: phase.confidence ?? 0.7,
            review: (phase.confidence ?? 0.7) < 0.62 ? "review" : "accepted",
            requires: phase.requires,
          }));
        if (!nextSteps.length) {
          setFileError("No workflow steps or phases were found in that JSON file.");
          return;
        }
        setWorkflowName(parsed.workflow_name ?? file.name);
        setNodes(nextSteps.map(stepToNode));
        setEdges(stepsToEdges(nextSteps));
        setSelectedNodeId(nextSteps[0].id);
        setFileError(null);
      } catch (error) {
        setFileError(error instanceof Error ? error.message : "Invalid workflow JSON.");
      }
    },
    [setEdges, setNodes],
  );

  const applyRun = useCallback(
    (run: AgentRun) => {
      const nextSteps = runToSteps(run);
      setAgentRun(run);
      setWorkflowName(run.objective);
      setNodes(nextSteps.map(stepToNode));
      setEdges(stepsToEdges(nextSteps));
      setSelectedNodeId(nextSteps[0]?.id ?? null);
      setRuntimeError(null);
    },
    [setEdges, setNodes],
  );

  const composeSampleRun = useCallback(async () => {
    setIsBusy(true);
    try {
      const run = await createAgentRun({
        objective: "Seal plate after expert review",
        binding: { sealer: "plateloc" },
        tasks: SAMPLE_AGENT_TASKS,
      });
      applyRun(run);
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : "Agent runtime request failed.");
    } finally {
      setIsBusy(false);
    }
  }, [applyRun]);

  const decideReview = useCallback(
    async (decision: "approve" | "reject") => {
      const review = agentRun?.workflow.review_requests[0];
      if (!agentRun || !review) return;
      setIsBusy(true);
      try {
        const nextRun =
          decision === "approve"
            ? await approveAgentReview(
                agentRun.id,
                review.task_id,
                "dashboard",
                "Approved from workflow canvas.",
              )
            : await rejectAgentReview(
                agentRun.id,
                review.task_id,
                "dashboard",
                "Rejected from workflow canvas.",
              );
        applyRun(nextRun);
      } catch (error) {
        setRuntimeError(error instanceof Error ? error.message : "Expert decision failed.");
      } finally {
        setIsBusy(false);
      }
    },
    [agentRun, applyRun],
  );

  const executeRun = useCallback(async () => {
    if (!agentRun) return;
    setIsBusy(true);
    try {
      applyRun(await executeAgentRun(agentRun.id));
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : "Execution failed.");
    } finally {
      setIsBusy(false);
    }
  }, [agentRun, applyRun]);

  return (
    <ReactFlowProvider>
      <div className="grid min-h-[720px] grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
          <header className="flex flex-col gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-800 lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Workflow DAG
              </p>
              <h2 className="truncate text-lg font-semibold text-ink dark:text-slate-100">
                {workflowName}
              </h2>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={isBusy}
                onClick={() => void composeSampleRun()}
                className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-100 dark:text-slate-950 dark:hover:bg-slate-300"
              >
                Compose run
              </button>
              <label className="inline-flex cursor-pointer items-center justify-center rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-ink transition-colors hover:bg-slate-50 dark:border-slate-700 dark:text-slate-100 dark:hover:bg-slate-900">
                Import JSON
                <input
                  type="file"
                  accept="application/json,.json"
                  className="sr-only"
                  onChange={(event) => void onFile(event.target.files?.[0] ?? null)}
                />
              </label>
            </div>
            {fileError ? (
              <p className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-100">
                {fileError}
              </p>
            ) : null}
            {runtimeError ? (
              <p className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-100">
                {runtimeError}
              </p>
            ) : null}
          </header>
          <div className="h-[620px] bg-slate-50 dark:bg-slate-950">
            <ReactFlow<WorkflowNode, Edge>
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={(_, node) => setSelectedNodeId(node.id)}
              fitView
              fitViewOptions={{ padding: 0.24 }}
            >
              <Background gap={22} color="rgba(100, 116, 139, 0.28)" />
              <MiniMap pannable zoomable className="!bg-white dark:!bg-slate-900" />
              <Controls className="!border-slate-200 !bg-white dark:!border-slate-800 dark:!bg-slate-900" />
            </ReactFlow>
          </div>
        </section>

        <aside className="flex flex-col gap-4">
          <section className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-950">
            <h2 className="text-sm font-semibold text-ink dark:text-slate-100">
              Plan quality
            </h2>
            <div className="mt-4 grid grid-cols-3 gap-2 text-center">
              <Metric label="Ready" value={acceptedCount} />
              <Metric label="Review" value={reviewCount} />
              <Metric label="Low" value={confidenceLabel(lowestConfidence)} />
            </div>
            {agentRun ? (
              <div className="mt-4 space-y-3">
                <Detail label="Run" value={agentRun.id} />
                <Detail label="State" value={agentRun.state} />
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={isBusy || agentRun.workflow.review_requests.length === 0}
                    onClick={() => void decideReview("approve")}
                    className="rounded-md border border-emerald-300 px-3 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-emerald-700 dark:text-emerald-200 dark:hover:bg-emerald-950"
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    disabled={isBusy || agentRun.workflow.review_requests.length === 0}
                    onClick={() => void decideReview("reject")}
                    className="rounded-md border border-rose-300 px-3 py-2 text-sm font-medium text-rose-800 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-rose-700 dark:text-rose-200 dark:hover:bg-rose-950"
                  >
                    Reject
                  </button>
                  <button
                    type="button"
                    disabled={isBusy || agentRun.state !== "ready"}
                    onClick={() => void executeRun()}
                    className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-ink hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:text-slate-100 dark:hover:bg-slate-900"
                  >
                    Dry-run
                  </button>
                </div>
              </div>
            ) : null}
          </section>

          <section className="min-h-[300px] rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-950">
            <h2 className="text-sm font-semibold text-ink dark:text-slate-100">
              Selected step
            </h2>
            {selected ? (
              <div className="mt-4 space-y-4 text-sm">
                <div>
                  <p className="font-semibold text-ink dark:text-slate-100">
                    {String(selected.label)}
                  </p>
                  <p className="mt-1 font-mono text-xs text-slate-500 dark:text-slate-400">
                    {String(selected.role)} · {String(selected.skill)}
                  </p>
                </div>
                <dl className="grid grid-cols-2 gap-3">
                  <Detail label="Confidence" value={confidenceLabel(Number(selected.confidence))} />
                  <Detail label="Gate" value={String(selected.review)} />
                </dl>
                {selected.args ? (
                  <pre className="max-h-56 overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-100">
                    {JSON.stringify(selected.args, null, 2)}
                  </pre>
                ) : null}
                {selected.expertNote ? (
                  <p className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs leading-5 text-amber-950 dark:border-amber-500/50 dark:bg-amber-950/30 dark:text-amber-100">
                    {String(selected.expertNote)}
                  </p>
                ) : null}
              </div>
            ) : (
              <p className="mt-4 text-sm text-slate-500 dark:text-slate-400">
                Select a node to inspect role, skill, confidence, and arguments.
              </p>
            )}
          </section>
        </aside>
      </div>
    </ReactFlowProvider>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border border-slate-200 px-2 py-3 dark:border-slate-800">
      <p className="text-lg font-semibold text-ink dark:text-slate-100">{value}</p>
      <p className="mt-1 text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </p>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </dt>
      <dd className="mt-1 font-medium text-ink dark:text-slate-100">{value}</dd>
    </div>
  );
}
