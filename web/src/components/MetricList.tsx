import type { MetricValue } from "@/types/api";

function formatValue(metric: MetricValue): string {
  if (typeof metric.value === "number") {
    const value = Number.isInteger(metric.value)
      ? metric.value.toString()
      : metric.value.toFixed(2);
    return metric.unit ? `${value} ${metric.unit}` : value;
  }
  return metric.unit ? `${metric.value} ${metric.unit}` : String(metric.value);
}

function humanKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function MetricList({ metrics }: { metrics: Record<string, MetricValue> }) {
  const entries = Object.entries(metrics);
  if (entries.length === 0) return null;
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
      {entries.map(([key, metric]) => (
        <div key={key} className="flex flex-col">
          <dt className="text-xs uppercase tracking-wide text-ink-subtle dark:text-slate-500">
            {humanKey(key)}
          </dt>
          <dd className="font-mono text-ink dark:text-slate-100">{formatValue(metric)}</dd>
        </div>
      ))}
    </dl>
  );
}
