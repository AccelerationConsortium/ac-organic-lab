"use client";

import { useUserAuth } from "@/lib/user-auth";

/**
 * Notebooks tab — embeds Bitácora (the agentic ELN), a separate Next.js app
 * served same-origin at /bitacora via Caddy path routing. It's a distinct
 * app (own server, own auth-gated route), so it's framed rather than linked
 * with next/link — a client-side route transition would try to resolve
 * /bitacora against this app's own route manifest and 404.
 *
 * The iframe itself is NOT rendered here: Bitácora keeps its interface in
 * client state (not the URL), so a page-owned frame would restart the ELN on
 * every dashboard tab switch. The root layout's <KeepAliveEmbeds /> owns the
 * frame and hides it with CSS between visits; this page contributes only the
 * auth-guard states (loading skeleton / sign-in card) and renders nothing
 * once signed in, leaving the column to the frame below the page slot.
 *
 * Server-side access is enforced twice already (the Next middleware redirects
 * signed-out visitors away from /notebooks; Bitácora's own route re-checks
 * its own auth) — the guard below is UX only, so a session expiring while
 * this tab is open shows the sign-in card instead of nesting Bitácora's own
 * auth screen inside this dashboard (the "russian doll").
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

  // Signed in: the keep-alive frame in the root layout is visible right here
  // in the same column; the page itself has nothing to add.
  return null;
}
