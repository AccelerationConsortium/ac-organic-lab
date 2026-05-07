import type { EquipmentSnapshot } from "@/types/api";
import { kindLabel } from "@/lib/format";
import { StatusPill } from "./StatusPill";
import { StalenessIndicator } from "./StalenessIndicator";
import { MetricList } from "./MetricList";
import { ComponentList } from "./ComponentList";

export function EquipmentStatusCard({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const { status } = snapshot;
  const metrics = status.metrics ?? {};
  const components = status.components ?? {};
  const requiredActions = status.required_actions ?? [];
  const hasMetrics = Object.keys(metrics).length > 0;
  const hasComponents = Object.keys(components).length > 0;

  return (
    <article className="flex h-full flex-col gap-4 overflow-hidden rounded-xl border border-slate-200 bg-surface-raised p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <header className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h3 className="text-base font-semibold text-ink dark:text-slate-100">
            {snapshot.name}
          </h3>
          <p className="text-xs text-ink-subtle dark:text-slate-500">
            {kindLabel(snapshot.kind)} · <span className="font-mono">{snapshot.id}</span>
          </p>
        </div>
        <StatusPill state={status.equipment_status} />
      </header>

      {status.message && (
        <p className="text-sm text-ink-muted dark:text-slate-300">{status.message}</p>
      )}

      {requiredActions.length > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200">
          <span className="font-medium">Action needed:</span>{" "}
          <span className="font-mono">{requiredActions.join(", ")}</span>
        </div>
      )}

      {hasMetrics && <MetricList metrics={metrics} />}
      {hasComponents && <ComponentList components={components} />}

      {snapshot.fetch_error && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
          <div className="font-medium">Aggregator could not reach device</div>
          <div className="font-mono">
            {snapshot.fetch_error.kind}
            {snapshot.fetch_error.http_status
              ? ` · HTTP ${snapshot.fetch_error.http_status}`
              : ""}
          </div>
        </div>
      )}

      <footer className="mt-auto flex items-center justify-between border-t border-slate-100 pt-3 text-xs text-ink-subtle dark:border-slate-800 dark:text-slate-500">
        <span>
          {snapshot.latency_ms != null ? `${snapshot.latency_ms} ms` : "—"}
        </span>
        <StalenessIndicator fetchedAt={snapshot.fetched_at} />
      </footer>
    </article>
  );
}
