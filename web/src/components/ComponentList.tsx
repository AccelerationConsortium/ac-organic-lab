import type { ComponentStatus } from "@/types/api";

function humanKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function ComponentList({
  components,
}: {
  components: Record<string, ComponentStatus>;
}) {
  const entries = Object.entries(components);
  if (entries.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1.5 text-sm">
      {entries.map(([key, comp]) => (
        <li key={key} className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                comp.connected
                  ? "bg-emerald-500 dark:bg-emerald-400"
                  : "bg-slate-300 dark:bg-slate-600"
              }`}
              aria-hidden
            />
            <span className="text-ink dark:text-slate-200">{humanKey(key)}</span>
          </div>
          <span className="font-mono text-xs text-ink-muted dark:text-slate-400">
            {comp.state}
          </span>
        </li>
      ))}
    </ul>
  );
}
