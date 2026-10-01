"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { request } from "./request";

type Decision = { by: string; outcome: "done" | "failed"; note: string; at: string };
export type ManualRequest = {
  request_id: string; step_id: string; state: string; since: string;
  manual: { title: string; instructions: string; confirmation_text: string; access_roles: string[] };
  custody?: { hid: string; from: string; to: string } | null;
  decision: Decision | null; result?: unknown;
};
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

