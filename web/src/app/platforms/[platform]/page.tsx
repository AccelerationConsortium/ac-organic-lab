"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { usePlatforms } from "@/lib/use-platforms";

/**
 * Placeholder for the per-platform **workflow UI** (`/platforms/{name}`).
 *
 * This route is reserved for running/monitoring workflows on a platform; the
 * old duplicate equipment-grid pages that lived here were removed — live
 * equipment tiles are on the Platforms tab. Every existing link
 * (platforms.yaml `href`, the Overview cards' "Open →", the camera "PTZ →")
 * already points here, so the workflow UI ships by replacing this file, with
 * no link churn.
 */
export default function PlatformWorkflowPlaceholder() {
  const params = useParams<{ platform: string }>();
  const { data: platforms } = usePlatforms();

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
        monitor experiments). Until then, live equipment tiles and controls are
        on the Platforms tab.
      </p>
      <Link
        href="/platforms"
        className="mt-2 rounded-md border border-sky-400 bg-sky-100 px-3 py-1.5 text-sm font-medium text-sky-900 transition-colors hover:bg-sky-200 dark:border-sky-600 dark:bg-sky-900/60 dark:text-sky-100 dark:hover:bg-sky-900/80"
      >
        Go to Platforms →
      </Link>
    </div>
  );
}
