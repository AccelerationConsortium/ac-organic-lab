"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { usePlatforms } from "@/lib/use-platforms";
import { useUserAuth } from "@/lib/user-auth";

/**
 * Placeholder for the per-platform **workflow UI** (`/platforms/{name}`).
 *
 * This route is reserved for running/monitoring runs on a platform; the
 * old duplicate equipment-grid pages that lived here were removed — live
 * equipment tiles are on the Platforms tab. Every existing link
 * (platforms.yaml `href`, the Overview cards' "GO →", the camera "GO →")
 * already points here, so the workflow UI ships by replacing this file, with
 * no link churn.
 */
export default function PlatformWorkflowPlaceholder() {
  const params = useParams<{ platform: string }>();
  const { data: platforms } = usePlatforms();
  const { loading, authenticated } = useUserAuth();

  const section = platforms?.sections.find(
    (s) => s.href === `/platforms/${params.platform}` || s.id === params.platform,
  );
  const title = section?.title ?? params.platform;

  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-slate-300 bg-surface-raised px-6 py-16 text-center dark:border-slate-700 dark:bg-slate-900">
      <span className="text-3xl" aria-hidden>
        🚧
      </span>
      <h2 className="text-lg font-semibold text-ink dark:text-slate-100">
        {title} — workflow UI under construction
      </h2>
      <p className="max-w-md text-sm text-ink-muted dark:text-slate-400">
        This page will host the {title} workflow interface (plan, run, and
        monitor experiments). Until then, experiments are planned and run in the
        Notebooks tab (Bitácora), and live equipment tiles and controls are on
        the Platforms tab.
      </p>
      <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
        {/* Same visibility rule as the Nav's Notebooks tab: /notebooks is
            sign-in-gated by the middleware (a signed-out click would just
            bounce to /), so show the link only to signed-in viewers and say
            why it's absent otherwise. */}
        {!loading && authenticated && (
          <Link
            href="/notebooks"
            className="rounded-md bg-orange-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-orange-700 dark:bg-orange-500 dark:hover:bg-orange-600"
          >
            Open the notebook →
          </Link>
        )}
        <Link
          href="/platforms"
          className="rounded-md border border-sky-400 bg-sky-100 px-3 py-1.5 text-sm font-medium text-sky-900 transition-colors hover:bg-sky-200 dark:border-sky-600 dark:bg-sky-900/60 dark:text-sky-100 dark:hover:bg-sky-900/80"
        >
          Go to Platforms →
        </Link>
      </div>
      {!loading && !authenticated && (
        <p className="text-xs text-ink-subtle dark:text-slate-500">
          Sign in to open the lab notebook.
        </p>
      )}
    </div>
  );
}
