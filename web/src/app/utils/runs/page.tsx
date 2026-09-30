"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/workflow${path}`, {
    ...(body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
  return response.json();
}

type Decision = { by: string; outcome: "done" | "failed"; note: string; at: string };
export type ManualRequest = {
  request_id: string; step_id: string; state: string; since: string;
  manual: { title: string; instructions: string; confirmation_text: string; access_roles: string[] };
  custody?: { hid: string; from: string; to: string } | null;
  decision: Decision | null; result?: unknown;
};
type Run = { run_id: string; status: string; waiting_on: ManualRequest | null; abort_requested: string | null; result?: unknown };

export function ManualCard({ runId, item, onChanged }: { runId: string; item: ManualRequest; onChanged: () => void }) {
  const [note, setNote] = useState("");
  const [checked, setChecked] = useState(false);
  const mutation = useMutation({
    mutationFn: (outcome: "done" | "failed") => request(`/runs/${encodeURIComponent(runId)}/manual/${encodeURIComponent(item.step_id)}`, {
      request_id: item.request_id, outcome: item.decision?.outcome ?? outcome,
      note: item.decision?.note ?? note,
    }),
    onSuccess: onChanged,
  });
  const waiting = item.state === "waiting" && !item.decision;
  const retry = item.state === "uncertain";
  return <section className="rounded border border-amber-500 p-4 space-y-3" aria-label="Manual action">
    <h2 className="font-semibold">{item.manual.title}</h2>
    <p className="text-sm">Step {item.step_id} · {item.state} · requested {item.since}</p>
    {item.custody && <p><strong>{item.custody.hid}</strong>: {item.custody.from} → {item.custody.to}</p>}
    <p className="whitespace-pre-wrap">{item.manual.instructions}</p>
    {item.manual.access_roles.length > 0 && <p className="text-sm">Access devices: {item.manual.access_roles.join(", ")}. Follow the reviewed instructions; stop if anything is unexpected.</p>}
    {item.decision && <p>Reported {item.decision.outcome} by {item.decision.by} at {item.decision.at}. {item.decision.note}</p>}
    {retry && <p role="alert">Completion was reported, but custody recording is unresolved. Do not repeat the physical move. Retry recording or abort and reconcile.</p>}
    {item.result != null && <pre className="overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(item.result, null, 2)}</pre>}
    {waiting && <>
      <label className="block">Note (optional)<textarea className="block w-full rounded border bg-transparent p-2" maxLength={4000} value={note} onChange={(e) => setNote(e.target.value)} /></label>
      <label className="flex gap-2"><input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />{item.manual.confirmation_text}</label>
    </>}
    <div className="flex flex-wrap gap-3">
      {waiting && <>
        <button className="rounded border px-3 py-2 disabled:opacity-40" disabled={!checked || mutation.isPending || mutation.isSuccess} onClick={() => mutation.mutate("done")}>Confirm completed</button>
        <button className="rounded border px-3 py-2 disabled:opacity-40" disabled={mutation.isPending || mutation.isSuccess} onClick={() => mutation.mutate("failed")}>Report failure</button>
      </>}
      {retry && <button className="rounded border px-3 py-2 disabled:opacity-40" disabled={mutation.isPending} onClick={() => mutation.mutate("done")}>Retry recording</button>}
    </div>
    {mutation.isError && <p role="alert">Acknowledgment was not confirmed: {mutation.error.message}. Retrying sends the same request; do not repeat the physical work.</p>}
    {mutation.isSuccess && <p role="status">Response accepted. Waiting for the runner&apos;s checks.</p>}
  </section>;
}

export default function RunsPage() {
  const [runId, setRunId] = useState("");
  const [lookup, setLookup] = useState("");
  const [authorization, setAuthorization] = useState("");
  const [dryRun, setDryRun] = useState(true);
  const cache = useQueryClient();
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("run_id") || "";
    setRunId(id); setLookup(id);
  }, []);
  function selectRun(id: string) {
    setRunId(id); setLookup(id);
    window.history.replaceState(null, "", `?run_id=${encodeURIComponent(id)}`);
  }
  const run = useQuery({ queryKey: ["workflow-run", runId], queryFn: () => request<Run>(`/runs/${encodeURIComponent(runId)}`), enabled: !!runId, refetchInterval: 2000, retry: false });
  const history = useQuery({ queryKey: ["manual-history", runId], queryFn: () => request<{ requests: ManualRequest[]; live: boolean }>(`/runs/${encodeURIComponent(runId)}/manual`), enabled: !!runId, refetchInterval: 3000, retry: false });
  const refresh = () => { void cache.invalidateQueries({ queryKey: ["workflow-run", runId] }); void cache.invalidateQueries({ queryKey: ["manual-history", runId] }); };
  const launch = useMutation({ mutationFn: () => request<{ run_id: string }>("/runs", { authorization_id: authorization.trim(), dry_run: dryRun }), onSuccess: (data) => selectRun(data.run_id) });
  const abort = useMutation({ mutationFn: () => request(`/runs/${encodeURIComponent(runId)}/abort`, {}), onSuccess: refresh });
  return <div className="mx-auto max-w-3xl space-y-5 p-4">
    <h1 className="text-xl font-semibold">Authorized runs</h1>
    <p>Run a previously approved authorization, or open a run to respond to its human actions. A restart interrupts a waiting run; saved decisions remain available for reconciliation.</p>
    <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); launch.mutate(); }}>
      <label className="block">Authorization ID<input className="block w-full rounded border bg-transparent p-2" required value={authorization} onChange={(e) => setAuthorization(e.target.value)} /></label>
      <label className="flex gap-2"><input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />Dry run — validate without equipment actions or human prompts</label>
      <button className="rounded border px-3 py-2 disabled:opacity-40" disabled={launch.isPending || !authorization.trim()}>{dryRun ? "Start dry run" : "Start authorized equipment run"}</button>
      {launch.isError && <p role="alert">Run start was not confirmed: {launch.error.message}. Check run history before attempting another live run.</p>}
    </form>
    <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); selectRun(lookup.trim()); }}>
      <label>Run ID<input className="ml-2 rounded border bg-transparent p-2" value={lookup} onChange={(e) => setLookup(e.target.value)} required /></label>
      <button className="rounded border px-3 py-2">Open run</button>
    </form>
    {runId && <>
      {run.isError && <p role="alert">Live run unavailable: {run.error.message}. A restarted runner cannot resume it; inspect saved manual decisions below.</p>}
      {!run.isError && run.data && <>
        <p role="status">{runId}: {run.data.status}{run.data.abort_requested ? " · abort requested" : ""}</p>
        {run.data.status === "running" && <button className="rounded border border-red-500 px-3 py-2" disabled={abort.isPending || !!run.data.abort_requested} onClick={() => abort.mutate()}>Abort run</button>}
        {run.data.waiting_on && <ManualCard key={run.data.waiting_on.request_id} runId={runId} item={run.data.waiting_on} onChanged={refresh} />}
        {run.data.result != null && <pre className="overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(run.data.result, null, 2)}</pre>}
      </>}
      {abort.isError && <p role="alert">Abort was not confirmed: {abort.error.message}</p>}
      <h2 className="font-semibold">Saved human actions</h2>
      {history.isError ? <p role="alert">Manual history unavailable: {history.error.message}</p> : history.data?.requests.map((item) => <div key={item.request_id} className="rounded border p-3">
        <p>{item.step_id}: {item.state}</p>
        {item.decision && <p>{item.decision.by} reported {item.decision.outcome}: {item.decision.note}</p>}
        {item.state === "interrupted" && <p>Reconcile the plate and device state before a new run. This request cannot resume execution.</p>}
      </div>)}
    </>}
  </div>;
}
