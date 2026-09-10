"use client";

/**
 * Dobot MG400 Control — the arm's own operator panel (a separate app served by
 * the device service at :8050/web/), framed here same-origin at /mg400/web/
 * via the Caddy path route. Framed rather than linked with next/link for the
 * same reason as /notebooks and xArm: a client-side transition would resolve
 * /mg400 against this app's route manifest and 404.
 *
 * Access is already enforced at the edge — /mg400/* sits behind forward_auth
 * against ac_auth, and the device trusts the injected identity (see
 * deploy/Caddyfile.single-edge), so the panel picks up the signed-in user
 * without a second login.
 */
export default function DobotControlPage() {
  return (
    // Full-bleed, like the xArm: the panel is a whole second UI (title tile,
    // motion graph, control cards, log) and the app's max-w-7xl column crops it.
    <div className="relative left-1/2 w-screen max-w-[100vw] -translate-x-1/2 px-4 sm:px-6 lg:px-8">
      {/* No border/radius/shadow: the panel draws its own tiles, so a frame
          around it reads as a second, redundant card edge. */}
      <iframe
        src="/mg400/web/"
        title="Dobot MG400 — Control Interface"
        className="h-[calc(100vh-180px)] min-h-[720px] w-full border-0 bg-transparent"
      />
    </div>
  );
}
