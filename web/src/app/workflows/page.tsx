"use client";

import { useUserAuth } from "@/lib/user-auth";

/**
 * Workflows tab — embeds Bitácora (the agentic ELN), a separate Next.js app
 * served same-origin at /bitacora via Caddy path routing. It's a distinct
 * app (own server, own auth-gated route), so it's framed here rather than
 * linked with next/link — a client-side route transition would try to
 * resolve /bitacora against this app's own route manifest and 404.
 *
 * Server-side access is enforced twice already (the Next middleware redirects
 * signed-out visitors away from /workflows; Bitácora's own route re-checks its
 * own auth) — the client-side guard below is UX only, mirroring /admin. It
 * exists so a session expiring while this page is already open doesn't leave
 * the iframe showing Bitácora's own auth-gated dashboard nested inside this
 * one (the "russian doll" this tab is otherwise prone to).
 */
export default function WorkflowsPage() {
  const { loading, authenticated } = useUserAuth();

  if (loading) {
    return <div className="mt-6 h-24 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />;
  }
  if (!authenticated) {
    return (
      <div className="mt-8 rounded-xl border border-dashed border-slate-300 p-10 text-center dark:border-slate-700">
        <p className="text-sm font-medium text-ink-muted dark:text-slate-400">
          Workflows — sign in to view Bitácora.
        </p>
      </div>
    );
  }

  return (
    // Full-bleed. Every other tab is content inside the app's `max-w-7xl`
    // column, but this one hosts an entire second app — a three-pane ELN with a
    // chat rail, a work surface and a plate grid — and 1280px squeezes all three.
    // `left-1/2 / -translate-x-1/2` centres on the viewport regardless of the
    // parent column, so the layout stays untouched for every other page; the
    // padding restores the gutter the container would have given.
    <div className="relative left-1/2 w-screen max-w-[100vw] -translate-x-1/2 px-4 sm:px-6 lg:px-8">
      <iframe
        src="/bitacora/"
        title="Bitácora — Agentic ELN"
        className="h-[calc(100vh-180px)] min-h-[520px] w-full rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800"
      />
    </div>
  );
}
