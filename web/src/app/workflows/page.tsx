"use client";

/**
 * Workflows tab — embeds Bitácora (the agentic ELN), a separate Next.js app
 * served same-origin at /bitacora via Caddy path routing. It's a distinct
 * app (own server, own auth-gated route), so it's framed here rather than
 * linked with next/link — a client-side route transition would try to
 * resolve /bitacora against this app's own route manifest and 404.
 */
export default function WorkflowsPage() {
  return (
    <iframe
      src="/bitacora/"
      title="Bitácora — Agentic ELN"
      className="h-[calc(100vh-220px)] min-h-[480px] w-full rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800"
    />
  );
}
