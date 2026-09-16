"use client";

import { useUserAuth } from "@/lib/user-auth";

/** The layout supplies SDL2 login; the API verifies the session independently. */
export default function RobotMotionPage() {
  const { loading, authenticated, requestLogin, canControl } = useUserAuth();
  function signIn() {
    requestLogin();
    // The shared SDL2 banner uses a shadow root, not the old React LoginBar.
    const slot = document.getElementById("ac-auth-banner-slot");
    slot?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    slot?.shadowRoot?.querySelector<HTMLSelectElement>("select")?.focus();
  }
  if (loading) return <p className="text-sm text-ink-subtle dark:text-slate-300">Checking your sign-in…</p>;
  if (!authenticated) {
    return (
      <div className="flex flex-col items-start gap-3 rounded-md border border-slate-200 bg-surface-subtle px-4 py-5 dark:border-slate-700 dark:bg-slate-800/40">
        <p className="text-sm text-ink-muted dark:text-slate-300">Sign in with your SDL2 account to open the UR5e control workspace.</p>
        <button type="button" onClick={signIn} className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-700">Sign in</button>
      </div>
    );
  }
  if (!canControl("ligand_ur5e")) {
    return <p role="status" className="text-sm text-ink-muted dark:text-slate-300">Your SDL2 account does not have access to the UR5e panel.</p>;
  }
  return (
    <div className="flex min-h-0 w-full flex-1 flex-col">
      {/* Explicit file avoids Next's trailing-slash redirect breaking relative assets. */}
      <iframe src="/api/robot-motion/ligand_ur5e/web/index.html" title="Robot Motion — UR5e Control Workspace"
        className="min-h-0 w-full flex-1 border-0 bg-transparent" />
    </div>
  );
}
