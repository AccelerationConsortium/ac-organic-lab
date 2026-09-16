"use client";

import { useUserAuth } from "@/lib/user-auth";

/** The root supplies SDL2's banner; the frame GET independently verifies sign-in. */
export default function HplcControlPage() {
  const { loading, authenticated, requestLogin } = useUserAuth();
  function signIn() {
    requestLogin();
    const slot = document.getElementById("ac-auth-banner-slot");
    slot?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    slot?.shadowRoot?.querySelector<HTMLSelectElement>("select")?.focus();
  }

  if (loading) return <p className="p-4 text-sm text-ink-subtle dark:text-slate-300">Checking your sign-in…</p>;
  if (!authenticated) return (
    <div className="flex flex-col items-start gap-3 p-4">
      <p className="text-sm text-ink-muted dark:text-slate-300">Sign in with your SDL2 account to open the HPLC control preview.</p>
      <button type="button" onClick={signIn} className="rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white hover:bg-sky-700">Sign in</button>
    </div>
  );

  return <iframe src="/equipment/lle_hplc/control/frame"
    title="HPLC acquisition control preview — simulated"
    sandbox="allow-scripts" referrerPolicy="no-referrer"
    className="min-h-0 w-full flex-1 border-0 bg-transparent" />;
}
