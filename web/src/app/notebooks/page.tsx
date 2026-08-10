"use client";

import { useUserAuth } from "@/lib/user-auth";

/**
 * Notebooks tab — embeds Bitácora (the agentic ELN), a separate Next.js app
 * served same-origin at /bitacora via Caddy path routing. It's a distinct
 * app (own server, own auth-gated route), so it's framed here rather than
 * linked with next/link — a client-side route transition would try to
 * resolve /bitacora against this app's own route manifest and 404.
 *
 * Server-side access is enforced twice already (the Next middleware redirects
 * signed-out visitors away from /notebooks; Bitácora's own route re-checks its
 * own auth) — the client-side guard below is UX only, mirroring /admin. It
 * exists so a session expiring while this page is already open doesn't leave
 * the iframe showing Bitácora's own auth-gated dashboard nested inside this
 * one (the "russian doll" this tab is otherwise prone to).
 */
export default function NotebooksPage() {
  const { loading, authenticated } = useUserAuth();

  if (loading) {
    return <div className="mt-6 h-24 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />;
  }
  if (!authenticated) {
    return (
      <div className="mt-8 rounded-xl border border-dashed border-slate-300 p-10 text-center dark:border-slate-700">
        <p className="text-sm font-medium text-ink-muted dark:text-slate-400">
          Notebooks — sign in to view Bitácora.
        </p>
      </div>
    );
  }

  return (
    // In the page's own column, not full-bleed: the ELN reads as one tab of
    // this dashboard rather than a second application that hijacks the window,
    // and its width then matches every other tab. It earns the space back by
    // floating its chat instead of docking it, so only the rail and the work
    // surface share the column.
    <div className="mt-4">
      {/* A card edge now that it sits in the column: without one, a framed app
          bleeds into the page and its own top bar reads as this page's. */}
      <iframe
        src="/bitacora/"
        title="Bitácora — Agentic ELN"
        className="h-[calc(100vh-190px)] min-h-[640px] w-full rounded-xl border border-slate-200 bg-transparent dark:border-slate-800"
      />
    </div>
  );
}
