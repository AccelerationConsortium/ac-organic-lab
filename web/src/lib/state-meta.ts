/**
 * Shared state metadata — colour + label + description for every equipment
 * state the dashboard renders (the STATUS_SPEC enum plus the reader-side
 * "unreachable" presentation state), and — since STATUS_SPEC v1.2 — the
 * orthogonal *activity* vocabulary (idle / running / unknown).
 *
 * Health and activity are independent questions ("is it healthy" vs "is it
 * working", spec §2.3) and get independent vocabularies here. Never merge
 * them into one pill with health's colour and activity's text.
 *
 * Single source of truth for the History page's bars/badges and the global
 * State Reference panel. Hex colours are used via inline styles so they are
 * never Tailwind-purged.
 */

import type { EquipmentSnapshot } from "@/types/api";

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

// ---------------------------------------------------------------------------
// Activity vocabulary (STATUS_SPEC v1.2 §2.3) — orthogonal to health.
// ---------------------------------------------------------------------------

export type ActivityName = "idle" | "running" | "unknown";

export const ACTIVITY_COLORS: Record<ActivityName, string> = {
  running: "#0ea5e9", // sky-500 — same hue as `busy` (busy ≡ healthy + running)
  idle:    "#cbd5e1", // slate-300 — quiet, observed
  unknown: "#94a3b8", // slate-400 — matches health `unknown`: no information
};

export const ACTIVITY_META: Record<ActivityName, {
  label: string;
  badge: string;
  desc: string;
}> = {
  running: { label: "Running", badge: "bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300",        desc: "Primary operation in progress (shaker: motor turning; sealer: seal cycle; …). Independent of health — a Degraded device can still be Running." },
  idle:    { label: "Idle",    badge: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",   desc: "Not performing its primary operation right now. Says nothing about health." },
  unknown: { label: "—",       badge: "bg-slate-100 text-slate-500 dark:bg-slate-800/50 dark:text-slate-500", desc: "Activity undetermined — the device predates spec v1.2 (doesn't report activity), is unreachable, or its components don't reveal it. Never assumed Idle." },
};

// ---------------------------------------------------------------------------
// Effective (reader-side) health state — shared by the History page and the
// Overview equipment rows so "unreachable" is attributed identically
// everywhere.
// ---------------------------------------------------------------------------

// Kinds reached over a secondary link behind a shared gateway
// (kasa-tapo-services fronts cameras + Kasa plugs). When the backing hardware
// is offline the gateway still answers /status (HTTP 200, so no transport
// `fetch_error`) but reports `equipment_status: "unknown"`. We attribute that
// `unknown` to a known reachability failure and render it as "unreachable" —
// matching how a directly-polled device surfaces a transport timeout. A bare
// `unknown` from a non-gateway device (cold start / not-yet-observed) stays
// "unknown". See STATUS_SPEC §"`unknown` vs `error` vs unreachable".
export const GATEWAY_FRONTED_KINDS = new Set(["camera", "power_strip", "smart_plug"]);

export function effectiveState(snap: EquipmentSnapshot): StateName {
  if (snap.fetch_error) return "unreachable";
  const reported = (snap.status?.equipment_status as StateName) ?? "unknown";
  if (reported === "unknown" && GATEWAY_FRONTED_KINDS.has(snap.kind)) {
    return "unreachable";
  }
  return reported;
}

/** Health states that warrant an attention glyph next to the activity label
 *  on compact surfaces (Overview equipment rows). `ready`/`busy` are nominal;
 *  `dry_run` is a deliberate mode, not a fault. */
const ATTENTION_STATES = new Set<StateName>([
  "requires_init", "degraded", "error", "e_stop", "unknown", "unreachable",
]);

export function stateNeedsAttention(state: StateName): boolean {
  return ATTENTION_STATES.has(state);
}
