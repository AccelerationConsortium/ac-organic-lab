"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type Session = { id: string; user: string; stream: string; mode: string; seconds: number; bytes_sent: number };
type Grant = { id: string; principal: string; streams: string[]; purpose: string; run_id: string | null; expires_in_seconds: number };

export default function CameraSessionsPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [error, setError] = useState("");
  const [principal, setPrincipal] = useState("");
  const [streams, setStreams] = useState("");
  const [purpose, setPurpose] = useState("");
  const [runId, setRunId] = useState("");
  const [duration, setDuration] = useState(60);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function call(path: string, init?: RequestInit) {
    const r = await fetch(`/api/camera-streams/${path}`, { ...init, cache: "no-store", credentials: "same-origin" });
    const body = r.status === 204 ? null : await r.json();
    if (!r.ok) throw new Error(body?.detail ?? `Request failed (${r.status})`);
    return body;
  }
  async function refresh() {
    try {
      const [s, g] = await Promise.all([call("sessions"), call("grants")]);
      setSessions(s.sessions); setGrants(g); setError("");
    } catch (e) { setError((e as Error).message); }
  }
  useEffect(() => { void refresh(); const t = setInterval(refresh, 10000); return () => clearInterval(t); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  async function remove(path: string) {
    try { await call(path, { method: "DELETE" }); await refresh(); }
    catch (e) { setError((e as Error).message); }
  }
  async function approve(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setMessage("");
    try {
      const g = await call("grants", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ principal, streams: streams.split(",").map(s => s.trim()).filter(Boolean), purpose,
          duration_seconds: duration * 60, run_id: runId || null }) });
      setMessage(`Monitoring approval: ${g.id}`); await refresh();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return <main className="mx-auto max-w-5xl space-y-6 p-6">
    <Link href="/admin" className="text-sky-400">← Administration</Link>
    <h1 className="text-2xl font-semibold">Camera viewing sessions</h1>
    <p>Viewing permissions do not grant camera movement or instrument control. Closing a session stops its video only.</p>
    {error && <p role="alert" className="text-rose-400">{error}</p>}
    <section className="space-y-2"><h2 className="text-lg font-semibold">Current viewers</h2>
      {!sessions.length && <p>No active viewing sessions.</p>}
      {sessions.map(s => <div key={s.id} className="flex flex-wrap items-center gap-3 rounded border border-slate-700 p-3">
        <span>{s.user} · {s.stream} · {s.mode} · {s.seconds}s · {(s.bytes_sent / 1048576).toFixed(1)} MiB proxied</span>
        <button className="text-rose-400" onClick={() => remove(`admin/sessions/${s.id}`)}>Disconnect session</button>
      </div>)}
    </section>
    <form onSubmit={approve} className="grid gap-3 rounded border border-slate-700 p-4">
      <h2 className="text-lg font-semibold">Approve background monitoring</h2>
      <p>Agents require a running dashboard workflow ID. Human kiosks may omit it. Approvals expire, are revoked when the run ends, and do not bypass camera capacity limits.</p>
      <label>Account email<input required value={principal} onChange={e => setPrincipal(e.target.value)} className="ml-2 bg-slate-800 p-2" /></label>
      <label>Camera feeds (comma-separated registry camera_id_lens_id)<input required value={streams} onChange={e => setStreams(e.target.value)} className="ml-2 w-full bg-slate-800 p-2" /></label>
      <label>Purpose<input required maxLength={300} value={purpose} onChange={e => setPurpose(e.target.value)} className="ml-2 w-full bg-slate-800 p-2" /></label>
      <label>Workflow run ID<input value={runId} onChange={e => setRunId(e.target.value)} className="ml-2 bg-slate-800 p-2" /></label>
      <label>Duration (minutes)<input type="number" min={1} max={1440} value={duration} onChange={e => setDuration(Number(e.target.value))} className="ml-2 bg-slate-800 p-2" /></label>
      <button disabled={busy} className="rounded bg-sky-800 p-2">{busy ? "Approving…" : "Approve monitoring"}</button>
      {message && <p role="status">{message}</p>}
    </form>
    <section className="space-y-2"><h2 className="text-lg font-semibold">Monitoring approvals</h2>
      {grants.map(g => <div key={g.id} className="space-y-2 rounded border border-slate-700 p-3">
        <p>{g.principal} · {g.purpose} · {Math.ceil(g.expires_in_seconds / 60)} minutes left</p>
        <p className="font-mono text-sm">{g.id}</p>
        {g.streams.map(stream => <Link key={stream} className="mr-3 text-sky-400" href={`/utils/camera-monitor?stream=${encodeURIComponent(stream)}&grant=${encodeURIComponent(g.id)}`}>Open {stream}</Link>)}
        <button className="text-rose-400" onClick={() => remove(`grants/${g.id}`)}>Revoke approval</button>
      </div>)}
    </section>
  </main>;
}
