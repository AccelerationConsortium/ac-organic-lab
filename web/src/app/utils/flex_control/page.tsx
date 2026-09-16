"use client";

import { useUserAuth } from "@/lib/user-auth";

/** The gateway owns the controls; Caddy authenticates every framed request. */
export default function FlexControlPage() {
  const { loading, authenticated, requestLogin, canControl } = useUserAuth();

  function signIn() {
    requestLogin();
    const slot = document.getElementById("ac-auth-banner-slot");
    slot?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    slot?.shadowRoot?.querySelector<HTMLSelectElement>("select")?.focus();
  }

  if (loading) {
    return <p className="text-sm text-ink-subtle dark:text-slate-300">Checking your sign-in…</p>;
  }
  if (!authenticated) {
    return (
      <div className="flex flex-col items-start gap-3 rounded-md border border-slate-200 bg-surface-subtle px-4 py-5 dark:border-slate-700 dark:bg-slate-800/40">
        <p className="text-sm text-ink-muted dark:text-slate-300">Sign in with your SDL2 account to open the Gibbie Flex control panel.</p>
        <button type="button" onClick={signIn} className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-700">Sign in</button>
      </div>
    );
  }
  if (!canControl("gibbie_flex")) {
    return <p role="status" className="text-sm text-ink-muted dark:text-slate-300">Your SDL2 account does not have access to the Gibbie Flex panel.</p>;
  }

  return (
    <div className="relative left-1/2 w-screen max-w-[100vw] -translate-x-1/2 px-4 sm:px-6 lg:px-8">
      <iframe
        src="/flex/gibbie/ui/"
        title="Opentrons Flex — Gibbie Control Interface"
        className="h-[calc(100vh-180px)] min-h-[720px] w-full border-0 bg-transparent"
      />
    </div>
  );
}
