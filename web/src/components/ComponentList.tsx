import type { ComponentStatus } from "@/types/api";

function humanKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

const CONTROL_MODE_LABELS: Record<string, string> = {
  robot_server: "HTTP",
  repl_session: "REPL",
};

export function ComponentList({
  components,
}: {
  components: Record<string, ComponentStatus>;
}) {
  const entries = Object.entries(components);
  if (entries.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1.5 text-sm">
      {entries.map(([key, comp]) => {
        const controlModeLabel = CONTROL_MODE_LABELS[key];
        const label = controlModeLabel ?? humanKey(key);
        return (
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
              <span className="text-ink dark:text-slate-200">{label}</span>
            </div>
            {controlModeLabel ? (
              <span
                aria-label={`${controlModeLabel} ${comp.connected ? "ON" : "OFF"}`}
                className={`rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wide ${
                  comp.connected
                    ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                    : "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300"
                }`}
              >
                {comp.connected ? "ON" : "OFF"}
              </span>
            ) : (
              <span className="text-xs text-ink-muted dark:text-slate-300">
                {comp.state}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
