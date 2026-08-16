"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { useUserAuth } from "@/lib/user-auth";

/**
 * Keep-alive iframes for the Bitácora embeds (/notebooks, /inventory).
 *
 * Bitácora keeps its interface — the open room, notebook, tab, search — in
 * client state, not in the URL (it has exactly two routes), so an iframe
 * owned by the page component restarts the ELN from scratch on every
 * dashboard tab switch, and no save/restore of the frame URL can help. The
 * only way to resume where the user left off is to never destroy the frame:
 * this component lives in the root layout, mounts each embed the first time
 * its tab is visited while signed in, and afterwards hides it with CSS
 * (`display:none` preserves an iframe's browsing context; unmounting kills
 * it) instead of unmounting when the user switches tabs.
 *
 * The route pages (/notebooks, /inventory) keep the auth-guard UI and render
 * nothing when signed in — the frame here occupies the same layout column
 * right below the page slot. Visibility requires `authenticated`, so a
 * session expiring mid-visit hides the frame rather than nesting Bitácora's
 * own login inside the dashboard (the russian doll the old page guard
 * existed for). The frame stays mounted through sign-out/sign-in, which is
 * fine: hiding is cosmetic, access is enforced by the edge and Bitácora.
 */
const EMBEDS = [
  { route: "/notebooks", src: "/bitacora/", title: "Bitácora — Agentic ELN" },
  { route: "/inventory", src: "/bitacora/inventory/embed", title: "Chemical inventory" },
] as const;

const FRAME_CLASS =
  "h-[calc(100vh-190px)] min-h-[640px] w-full rounded-xl border border-slate-200 bg-transparent dark:border-slate-800";

export function KeepAliveEmbeds() {
  const pathname = usePathname();
  const { authenticated } = useUserAuth();
  const [mounted, setMounted] = useState<Record<string, boolean>>({});

  const activeRoute = EMBEDS.find(
    (e) => pathname === e.route || pathname.startsWith(`${e.route}/`),
  )?.route;

  // Lazy first mount: a user who never opens these tabs never loads the ELN.
  useEffect(() => {
    if (activeRoute && authenticated) {
      setMounted((m) => (m[activeRoute] ? m : { ...m, [activeRoute]: true }));
    }
  }, [activeRoute, authenticated]);

  return (
    <>
      {EMBEDS.filter((e) => mounted[e.route]).map((e) => {
        const visible = authenticated && activeRoute === e.route;
        return (
          <div key={e.route} hidden={!visible}>
            <iframe src={e.src} title={e.title} className={FRAME_CLASS} />
          </div>
        );
      })}
    </>
  );
}
