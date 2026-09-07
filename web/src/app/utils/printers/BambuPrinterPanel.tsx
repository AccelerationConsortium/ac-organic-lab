"use client";

import { EquipmentGrid } from "@/components/EquipmentGrid";
import { useUserAuth } from "@/lib/user-auth";
import type { EquipmentSnapshot } from "@/types/api";

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

/**
 * The same page reached directly on the gateway. Kept as a fallback for when
 * the edge route is not installed, and deliberately an absolute tailnet URL:
 * `equipment.yaml` reaches that gateway on loopback, which in a browser is the
 * visitor's own machine. Submissions made this way are *not* attributable —
 * there is no login on that port.
 */
const SUBMISSION_DIRECT_URL = "http://100.64.254.6:8012/ui";

export function BambuPrinterPanel({ printers }: { printers: EquipmentSnapshot[] }) {
  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink dark:text-slate-100">
            Bambu Printers
          </h1>
          <p className="text-sm text-ink-subtle dark:text-slate-300">
            Monitoring only · live local MQTT telemetry from the Bambu Gateway. Printer
            controls remain in Bambu&apos;s cloud interfaces.
          </p>
          <p className="text-sm text-ink-subtle dark:text-slate-300">
            Print jobs are submitted below: the gateway validates a model against the
            target machine and queues it. Queued jobs are never sent to a printer —
            dispatch is not implemented.
          </p>
        </div>
        <a
          href={SUBMISSION_DIRECT_URL}
          target="_blank"
          rel="noreferrer"
          className="shrink-0 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-ink hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700"
        >
          Open directly <span aria-hidden="true">↗</span>
        </a>
      </header>

      <EquipmentGrid snapshots={printers} />

      <SubmissionPanel />
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
function SubmissionPanel() {
  const { loading, authenticated, requestLogin } = useUserAuth();

  return (
    <section className="flex flex-col gap-2">
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
            The gateway&apos;s own page, served here behind the dashboard&apos;s login,
            so a submission is recorded against your account. If this panel is blank
            the{" "}
            <code className="rounded bg-slate-100 px-1 py-0.5 text-xs dark:bg-slate-800">
              /bambu/*
            </code>{" "}
            edge route is not installed yet — use <em>Open directly</em> above, where
            submissions are not attributable.
          </p>
          {/* No border or radius: the page draws its own panels, so a frame
              around it reads as a second, redundant card edge. Same reasoning
              as /utils/xarm_control. */}
          <iframe
            src={SUBMISSION_EMBED_PATH}
            title="Bambu Gateway — submit a print"
            className="h-[880px] min-h-[560px] w-full border-0 bg-transparent"
          />
        </>
      ) : (
        <div className="flex flex-col items-start gap-3 rounded-md border border-slate-200 bg-surface-subtle px-4 py-5 dark:border-slate-700 dark:bg-slate-800/40">
          <p className="text-sm text-ink-muted dark:text-slate-300">
            Sign in to submit a print. Submissions made here are recorded against
            your account; the <em>Open directly</em> link above reaches the gateway
            without a login, and those are not attributable to anyone.
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
