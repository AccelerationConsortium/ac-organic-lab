"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useUserAuth } from "@/lib/user-auth";
import type { EquipmentSnapshot } from "@/types/api";
import { BambuPrinterTile } from "./BambuPrinterTile";

/**
 * The gateway's submission page, framed same-origin at /bambu/ui/ via a Caddy
 * path route — the same arrangement as the xArm panel at /xarm5/web/.
 *
 * Framing it behind the edge is what buys SSO. A cookie cannot be shared with
 * the gateway on its own origin: raw 100.x addresses cannot carry a `Domain`
 * cookie and *.ts.net is on the Public Suffix List, so a browser drops any
 * tailnet-wide cookie (see AUTH_DESIGN, "Why sessions can't be shared
 * per-host"). One origin behind /bambu/* means one login, and the edge injects
 * the signed-in identity so a submission is recorded against a real account
 * instead of a name somebody typed.
 */
const SUBMISSION_EMBED_PATH = "/bambu/ui/";

export function BambuPrinterPanel({ printers }: { printers: EquipmentSnapshot[] }) {
  const [selected, setSelected] = useState<{ id: string } | null>(null);
  const select = (id: string) => {
    setSelected({ id });
    document.getElementById("printer-submission")?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };
  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink dark:text-slate-100">
            Bambu Printers
          </h1>
          <p className="text-sm text-ink-subtle dark:text-slate-300">
            Live print progress, temperatures, and filament inventory.
          </p>
          <p className="text-sm text-ink-subtle dark:text-slate-300">
            Monitoring only · prepare and queue a model below. Queued jobs are not
            sent to a printer; dispatch is not implemented.
          </p>
        </div>
        <a
          href={SUBMISSION_EMBED_PATH}
          target="_blank"
          rel="noreferrer"
          className="shrink-0 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-ink hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700"
        >
          Open submissions <span aria-hidden="true">↗</span>
        </a>
      </header>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {printers.map(printer => <BambuPrinterTile key={printer.id} snapshot={printer} onSelect={() => select(printer.id)} />)}
        {printers.length === 0 && <p className="text-sm text-ink-subtle dark:text-slate-400">No printers registered.</p>}
      </div>

      <SubmissionPanel selected={selected} />
    </section>
  );
}

/**
 * The framed page, or an explanation of why it is not framed.
 *
 * Gating on the session is not cosmetic. The `/bambu/*` route sits behind
 * `forward_auth`, and an unauthenticated request comes back 401 carrying the
 * dashboard's own login HTML as its body — which a browser happily renders,
 * so framing it unconditionally showed the dashboard nested inside itself
 * (with its layout scripts re-running in the frame). We only frame it when we
 * know the request will be allowed through.
 */
function SubmissionPanel({ selected }: { selected: { id: string } | null }) {
  const { loading, authenticated, requestLogin } = useUserAuth();
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(1100);
  const syncTheme = useCallback(() => frame.current?.contentWindow?.postMessage({
    type: "bambu:theme", theme: document.documentElement.classList.contains("dark") ? "dark" : "light",
  }, window.location.origin), []);
  const syncSelection = useCallback(() => {
    if (selected) frame.current?.contentWindow?.postMessage({type: "bambu:select-printer", printer: selected.id}, window.location.origin);
  }, [selected]);
  useEffect(() => {
    const observer = new MutationObserver(syncTheme);
    observer.observe(document.documentElement, {attributes: true, attributeFilter: ["class"]});
    return () => observer.disconnect();
  }, [syncTheme]);
  useEffect(syncSelection, [syncSelection]);
  useEffect(() => {
    const resize = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.source !== frame.current?.contentWindow) return;
      if (event.data?.type === "bambu:height" && typeof event.data.height === "number" && Number.isFinite(event.data.height)) {
        setHeight(Math.max(640, Math.min(4000, Math.ceil(event.data.height))));
      }
    };
    window.addEventListener("message", resize);
    return () => window.removeEventListener("message", resize);
  }, []);

  return (
    <section id="printer-submission" className="flex scroll-mt-6 flex-col gap-3">
      <h2 className="text-base font-semibold text-ink dark:text-slate-100">
        Submit a print
      </h2>

      {loading ? (
        <p className="text-sm text-ink-subtle dark:text-slate-300">
          Checking your sign-in…
        </p>
      ) : authenticated ? (
        <>
          <p className="text-sm text-ink-subtle dark:text-slate-300">
            Validate your model for a printer, review the results, and manage its queue.
            Submissions are recorded against your signed-in account.
          </p>
          {/* No border or radius: the page draws its own panels, so a frame
              around it reads as a second, redundant card edge. Same reasoning
              as /utils/xarm_control. */}
          <iframe
            ref={frame}
            src={`${SUBMISSION_EMBED_PATH}?embed=1`}
            onLoad={() => { syncTheme(); syncSelection(); }}
            title="Bambu Gateway — submit a print"
            className="min-h-[640px] w-full rounded-xl border-0 bg-transparent"
            style={{height}}
          />
        </>
      ) : (
        <div className="flex flex-col items-start gap-3 rounded-md border border-slate-200 bg-surface-subtle px-4 py-5 dark:border-slate-700 dark:bg-slate-800/40">
          <p className="text-sm text-ink-muted dark:text-slate-300">
            Sign in to submit a print. Submissions made here are recorded against
            your account. The submission page uses the same dashboard sign-in.
          </p>
          <button
            type="button"
            onClick={requestLogin}
            className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-700"
          >
            Sign in
          </button>
        </div>
      )}
    </section>
  );
}
