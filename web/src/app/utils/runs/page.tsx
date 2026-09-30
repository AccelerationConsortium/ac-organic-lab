"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ManualCard, type ManualRequest } from "./manual-card";
import { request } from "./request";

type Run = { run_id: string; status: string; waiting_on: ManualRequest | null; abort_requested: string | null; result?: unknown };

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
