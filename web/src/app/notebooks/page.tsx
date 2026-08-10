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
    // Full-bleed, like /utils/xarm_control: the ELN is a whole second app (a
    // three-pane dashboard with a chat rail, work surface and plate grid) and
    // the app's max-w-7xl column squeezes all three. `left-1/2 / -translate-x-1/2`
    // centres on the viewport regardless of the parent column; the padding
    // restores the gutter the container would have given.
    <div className="relative left-1/2 w-screen max-w-[100vw] -translate-x-1/2 px-4 sm:px-6 lg:px-8">
      {/* No border/radius/shadow: Bitácora draws its own dashboard chrome, so
          a frame around it reads as a second, redundant card edge. */}
      <iframe
        src="/bitacora/"
        title="Bitácora — Agentic ELN"
        className="h-[calc(100vh-180px)] min-h-[720px] w-full border-0 bg-transparent"
      />
    </div>
  );
}
