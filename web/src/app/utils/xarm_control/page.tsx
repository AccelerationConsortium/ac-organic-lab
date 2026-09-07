"use client";

import { useUserAuth } from "@/lib/user-auth";

/**
 * xArm Control — the arm's own operator panel (a separate app served by the
 * device service at :8000/web/), framed here same-origin at /xarm5/web/ via
 * the Caddy path route. Framed rather than linked with next/link for the same
 * reason as /notebooks: a client-side transition would resolve /xarm5 against
 * this app's route manifest and 404.
 *
 * Access is already enforced at the edge — /xarm5/* sits behind forward_auth
 * against ac_auth, and the device trusts the injected identity (see
 * deploy/Caddyfile.single-edge), so the panel picks up the signed-in user
 * without a second login.
 *
 * Which is exactly why the frame is gated on the session here. An
 * unauthenticated request to /xarm5/web/ comes back `401 {"detail":"not
 * authenticated"}`, and a browser renders that body — so framing it
 * unconditionally showed a logged-out visitor raw JSON where the panel should
 * be. We only frame it once we know the request will be allowed through.
 */
export default function XarmControlPage() {
  const { loading, authenticated, requestLogin } = useUserAuth();

  if (loading) {
    return (
      <p className="text-sm text-ink-subtle dark:text-slate-300">
        Checking your sign-in…
      </p>
    );
  }

  if (!authenticated) {
    return (
      <div className="flex flex-col items-start gap-3 rounded-md border border-slate-200 bg-surface-subtle px-4 py-5 dark:border-slate-700 dark:bg-slate-800/40">
        <p className="text-sm text-ink-muted dark:text-slate-300">
          Sign in to open the arm&apos;s control panel. The edge passes your identity
          through to the device, so a claim you take is recorded against your
          account.
        </p>
        <button
          type="button"
          onClick={requestLogin}
          className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-700"
        >
          Sign in
        </button>
      </div>
    );
  }

  return (
    // Full-bleed, like /notebooks: the panel is a whole second UI (title tile,
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
