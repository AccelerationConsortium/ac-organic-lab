"use client";

import type { ReactNode } from "react";

// ---------------------------------------------------------------------------
// Card chrome for the admin console's tiles, in the Overview page's
// vocabulary (PlatformCard / LabEnvironmentCard): rounded-xl card, p-5,
// shadow-sm, a `text-base` semibold title over a `text-xs` subtle
// description, `gap-4` rhythm. The body is a framed, fixed-height scroll
// panel (`max-h-80`) so a tile keeps its footprint while a long table
// scrolls under a sticky header — pass `frame={false}` for content that is
// not a table (the KPI grid).
// ---------------------------------------------------------------------------

export function AdminTile({
  title,
  sub,
  controls,
  frame = true,
  className = "",
  children,
}: {
  title: string;
  sub?: string;
  /** Optional header widgets (filter dropdowns, a GO → link). */
  controls?: ReactNode;
  /** Wrap the body in the bordered scroll panel (default). */
  frame?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      className={`flex flex-col gap-3 rounded-xl border border-slate-200 bg-surface-raised p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900 ${className}`}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-ink dark:text-slate-100">{title}</h2>
          {sub && <p className="text-xs text-ink-subtle dark:text-slate-400">{sub}</p>}
        </div>
        {controls && <div className="flex shrink-0 items-center gap-2">{controls}</div>}
      </header>
      {frame ? (
        <div className="max-h-80 overflow-auto rounded-md border border-slate-100 bg-white/60 dark:border-slate-800 dark:bg-slate-950/40">
          {children}
        </div>
      ) : (
        children
      )}
    </section>
  );
}

/**
 * One cell of a KPI row: sentence-case label, a semibold value in the page
 * sans (proportional figures — these are not a column), and a short muted
 * detail line saying what the number is over. Sizes follow PlatformCard's
 * small-label scale (text-xs label, text-[10px] detail).
 */
export function Stat({
  label,
  value,
  detail,
  title,
}: {
  label: string;
  value: string;
  detail?: string;
  title?: string;
}) {
  return (
    <div className="min-w-0" title={title}>
      <dt className="truncate text-xs text-ink-subtle dark:text-slate-400">{label}</dt>
      <dd className="mt-0.5 truncate text-2xl font-semibold leading-tight text-ink dark:text-slate-100">
        {value}
      </dd>
      {detail && (
        <dd className="mt-0.5 text-[10px] leading-snug text-ink-muted dark:text-slate-300">
          {detail}
        </dd>
      )}
    </div>
  );
}

export function Empty({ message }: { message: string }) {
  return (
    <p className="px-3 py-5 text-center text-sm text-ink-subtle dark:text-slate-400">{message}</p>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  return (
    <p className="px-3 py-5 text-center text-sm text-rose-600 dark:text-rose-400">
      {error instanceof Error ? error.message : "Failed to load."}
    </p>
  );
}
