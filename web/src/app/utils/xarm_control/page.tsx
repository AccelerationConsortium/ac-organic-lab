"use client";

/**
 * xArm Control — the arm's own operator panel (a separate app served by the
 * device service at :8000/web/), framed here same-origin at /xarm5/web/ via
 * the Caddy path route. Framed rather than linked with next/link for the same
 * reason as /workflows: a client-side transition would resolve /xarm5 against
 * this app's route manifest and 404.
 *
 * Access is already enforced at the edge — /xarm5/* sits behind forward_auth
 * against ac_auth, and the device trusts the injected identity (see
 * deploy/Caddyfile.single-edge), so the panel picks up the signed-in user
 * without a second login.
 */
export default function XarmControlPage() {
  return (
    // Full-bleed, like /workflows: the panel is a whole second UI (title tile,
    // camera, Control Modes, log) and the app's max-w-7xl column crops it.
    <div className="relative left-1/2 w-screen max-w-[100vw] -translate-x-1/2 px-4 sm:px-6 lg:px-8">
      {/* No border/radius/shadow: the panel draws its own tiles, so a frame
          around it reads as a second, redundant card edge. */}
      <iframe
        src="/xarm5/web/"
        title="xArm Translocation — Control Interface"
        className="h-[calc(100vh-180px)] min-h-[720px] w-full border-0 bg-transparent"
      />
    </div>
  );
}
