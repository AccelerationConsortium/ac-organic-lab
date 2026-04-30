import type { EquipmentSnapshot, EquipmentState } from "@/types/api";

const stateOrder: EquipmentState[] = [
  "error",
  "e_stop",
  "degraded",
  "requires_init",
  "busy",
  "ready",
  "dry_run",
  "unknown",
];

const stateLabels: Record<EquipmentState, string> = {
  ready: "ready",
  busy: "busy",
  requires_init: "needs init",
  degraded: "degraded",
  dry_run: "dry run",
  error: "error",
  e_stop: "e-stop",
  unknown: "unknown",
};

const stateAccent: Record<EquipmentState, string> = {
  ready: "text-emerald-700 dark:text-emerald-400",
  busy: "text-sky-700 dark:text-sky-400",
  requires_init: "text-amber-700 dark:text-amber-400",
  degraded: "text-orange-700 dark:text-orange-400",
  dry_run: "text-violet-700 dark:text-violet-400",
  error: "text-rose-700 dark:text-rose-400",
  e_stop: "text-red-700 dark:text-red-400",
  unknown: "text-slate-600 dark:text-slate-400",
};

export function PlatformSummary({
  title,
  snapshots,
  href,
}: {
  title: string;
  snapshots: EquipmentSnapshot[];
  href?: string;
}) {
  const counts = new Map<EquipmentState, number>();
  for (const s of snapshots) {
    const state = s.status.equipment_status;
    counts.set(state, (counts.get(state) ?? 0) + 1);
  }

  const summary = stateOrder
    .filter((state) => (counts.get(state) ?? 0) > 0)
    .map((state) => ({ state, count: counts.get(state)! }));

  return (
    <section className="rounded-xl border border-slate-200 bg-surface-raised p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <header className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-ink dark:text-slate-100">{title}</h2>
        {href && (
          <a
            href={href}
            className="text-sm text-sky-700 hover:underline dark:text-sky-400"
          >
            Open platform →
          </a>
        )}
      </header>
      <p className="mt-1 text-sm text-ink-subtle dark:text-slate-400">
        {snapshots.length} equipment
      </p>
      <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm">
        {summary.length === 0 && (
          <span className="text-ink-subtle dark:text-slate-500">—</span>
        )}
        {summary.map(({ state, count }) => (
          <div key={state} className="flex items-baseline gap-1.5">
            <span className={`font-mono text-base font-semibold ${stateAccent[state]}`}>
              {count}
            </span>
            <span className="text-xs uppercase tracking-wide text-ink-subtle dark:text-slate-500">
              {stateLabels[state]}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
