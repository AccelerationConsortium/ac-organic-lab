import type { ReactNode } from "react";
import { StalenessIndicator } from "@/components/StalenessIndicator";

/** Shared presentation for printer telemetry and connection-only monitors. */
export function PrinterTileFrame({ name, subtitle, badge, fetchedAt, staleAfterSeconds, children }: {
  name: string;
  subtitle: ReactNode;
  badge?: ReactNode;
  fetchedAt?: string;
  staleAfterSeconds?: number;
  children: ReactNode;
}) {
  return (
    <article className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-900">
      <header className="border-b border-slate-100 px-3 py-2.5 dark:border-slate-800">
        <div className="flex items-start justify-between gap-2">
          <h2 className="min-w-0 text-sm font-semibold text-ink dark:text-slate-100">{name}</h2>
          {badge && <div className="shrink-0">{badge}</div>}
        </div>
        <p className="mt-0.5 flex flex-wrap gap-x-1 text-xs text-ink-subtle dark:text-slate-400">{subtitle}</p>
      </header>
      <div className="flex flex-col gap-2.5 p-3">{children}</div>
      {fetchedAt && <footer className="border-t border-slate-100 px-3 py-2 text-xs text-ink-subtle dark:border-slate-800 dark:text-slate-400">
        <StalenessIndicator fetchedAt={fetchedAt} staleAfterSeconds={staleAfterSeconds} />
      </footer>}
    </article>
  );
}

export function PrinterTileMetrics({ items }: { items: { label: string; value: string; wide?: boolean }[] }) {
  return (
    <dl className="flex gap-3 rounded-lg bg-slate-50 px-2.5 py-2 dark:bg-slate-800/60">
      {items.map(({ label, value, wide }) => (
        <div key={label} className={`min-w-0 ${wide ? "flex-[2]" : "flex-1"}`}>
          <dt className="text-xs text-ink-subtle dark:text-slate-400">{label}</dt>
          <dd className="break-words text-sm font-semibold tabular-nums text-ink dark:text-slate-100">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
