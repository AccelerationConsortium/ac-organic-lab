"use client";

import { useEffect, useState } from "react";
import { CameraPlayer } from "@/components/CameraPlayer";

export default function CameraMonitorPage() {
  const [stream, setStream] = useState("");
  const [grant, setGrant] = useState("");
  const [viewing, setViewing] = useState(false);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    setStream(q.get("stream") ?? ""); setGrant(q.get("grant") ?? "");
  }, []);
  return <main className="mx-auto max-w-5xl space-y-4 p-6">
    <h1 className="text-2xl font-semibold">Approved camera monitor</h1>
    <p>Sign in with the approved account. Background viewing continues only while your monitoring approval and session remain valid.</p>
    <label>Camera feed<input disabled={viewing} value={stream} onChange={e => setStream(e.target.value)} className="ml-2 bg-slate-800 p-2" /></label>
    <label>Approval ID<input disabled={viewing} value={grant} onChange={e => setGrant(e.target.value)} className="ml-2 bg-slate-800 p-2" /></label>
    <button disabled={!stream || !grant} onClick={() => setViewing(!viewing)} className="rounded bg-sky-800 p-2">{viewing ? "Stop monitoring" : "Start approved monitoring"}</button>
    {viewing && <CameraPlayer src={`/streams/api/ws?src=${encodeURIComponent(stream)}`} grantId={grant} />}
  </main>;
}
