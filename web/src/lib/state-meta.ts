/**
 * Shared state metadata — colour + label + description for every equipment
 * state the dashboard renders (the STATUS_SPEC enum plus the reader-side
 * "unreachable" presentation state).
 *
 * Single source of truth for the History page's bars/badges and the global
 * State Reference panel. Hex colours are used via inline styles so they are
 * never Tailwind-purged.
 */

export type StateName =
  | "ready" | "busy" | "requires_init" | "degraded"
  | "dry_run" | "error" | "e_stop" | "unknown" | "unreachable";

export const STATE_COLORS: Record<StateName, string> = {
  ready:         "#10b981", // emerald-500
  busy:          "#0ea5e9", // sky-500
  requires_init: "#fbbf24", // amber-400
  degraded:      "#f97316", // orange-500
  dry_run:       "#8b5cf6", // violet-500
  error:         "#f43f5e", // rose-500
  e_stop:        "#b91c1c", // red-700
  unknown:       "#94a3b8", // slate-400
  unreachable:   "#fb7185", // rose-400
};

export const STATE_META: Record<StateName, {
  label: string;
  dot: string;
  badge: string;
  desc: string;
}> = {
  ready:         { label: "Ready",        dot: "bg-emerald-500",  badge: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300", desc: "Idle and ready to accept commands." },
  busy:          { label: "Busy",         dot: "bg-sky-500",      badge: "bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300",                 desc: "Executing a protocol or operation." },
  requires_init: { label: "Needs Init",   dot: "bg-amber-400",    badge: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",         desc: "Requires initialization before use." },
  degraded:      { label: "Degraded",     dot: "bg-orange-500",   badge: "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300",     desc: "Reachable but operating in reduced capacity." },
  dry_run:       { label: "Dry Run",      dot: "bg-violet-500",   badge: "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300",     desc: "Simulating operations without physical actuation." },
  error:         { label: "Error",        dot: "bg-rose-500",     badge: "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300",             desc: "Device reported an internal error — check device logs." },
  e_stop:        { label: "E-Stop",       dot: "bg-red-700",      badge: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",                 desc: "Emergency stop active — physical inspection required." },
  unknown:       { label: "Unknown",      dot: "bg-slate-400",    badge: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",            desc: "State genuinely undetermined — cold start before the first successful poll, or unobserved time before the aggregator began monitoring. NOT a failure on its own; counted as up for uptime %. (A device known to be offline shows as Unreachable instead.)" },
  unreachable:   { label: "Unreachable",  dot: "bg-rose-400",     badge: "bg-rose-50 text-rose-600 dark:bg-rose-900/20 dark:text-rose-400",             desc: "Device is offline — either the aggregator's /status poll failed at the transport layer (timeout / connection refused), or a gateway reports it cannot reach the backing hardware (camera / plug). This is what 'offline' means here, not Unknown. Counted as down." },
};
