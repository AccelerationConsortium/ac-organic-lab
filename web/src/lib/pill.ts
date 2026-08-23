import type { EquipmentSnapshot, EquipmentState } from "@/types/api";

/**
 * Shared "Platforms-style" pill format. One source of truth for the bordered,
 * rounded-full pill used by the Platforms tab, the Overview filter row, and the
 * History section tabs, so the three read as one system.
 *
 * `tone` colours the active/selected state:
 *   - "sky"   (default) — selection pills (platform tabs, section filters).
 *   - "green" — instrument-visibility pills: green means "this tile is shown".
 */
export function pillClass(active: boolean, tone: "sky" | "green" = "sky"): string {
  // Active tints mirror the PositionPill "current" palette (border-*-400
  // bg-*-100 text-*-900) so pills and tile buttons read as one system.
  const activeClass =
    tone === "green"
      ? "border-emerald-400 bg-emerald-100 text-emerald-900 hover:bg-emerald-200 dark:border-emerald-600 dark:bg-emerald-900/60 dark:text-emerald-100 dark:hover:bg-emerald-900/80"
      : "border-sky-400 bg-sky-100 text-sky-900 hover:bg-sky-200 dark:border-sky-600 dark:bg-sky-900/60 dark:text-sky-100 dark:hover:bg-sky-900/80";
  return [
    "flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors",
    active
      ? activeClass
      : "border-slate-300 bg-white text-ink hover:border-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:border-slate-500",
  ].join(" ");
}

/**
 * Active-tint classes for a per-equipment visibility pill, keyed by the
 * equipment's current state — an "on" tile pill is drawn in the instrument's
 * status colour (ready emerald, busy sky, degraded orange, error rose, …)
 * rather than a single flat green. Mirrors the PositionPill "current" palette
 * used by {@link pillClass}, per state.
 */
const STATE_ACTIVE_PILL: Record<EquipmentState, string> = {
  ready:
    "border-emerald-400 bg-emerald-100 text-emerald-900 hover:bg-emerald-200 dark:border-emerald-600 dark:bg-emerald-900/60 dark:text-emerald-100 dark:hover:bg-emerald-900/80",
  busy: "border-sky-400 bg-sky-100 text-sky-900 hover:bg-sky-200 dark:border-sky-600 dark:bg-sky-900/60 dark:text-sky-100 dark:hover:bg-sky-900/80",
  requires_init:
    "border-amber-400 bg-amber-100 text-amber-900 hover:bg-amber-200 dark:border-amber-600 dark:bg-amber-900/60 dark:text-amber-100 dark:hover:bg-amber-900/80",
  degraded:
    "border-orange-400 bg-orange-100 text-orange-900 hover:bg-orange-200 dark:border-orange-600 dark:bg-orange-900/60 dark:text-orange-100 dark:hover:bg-orange-900/80",
  dry_run:
    "border-violet-400 bg-violet-100 text-violet-900 hover:bg-violet-200 dark:border-violet-600 dark:bg-violet-900/60 dark:text-violet-100 dark:hover:bg-violet-900/80",
  error:
    "border-rose-400 bg-rose-100 text-rose-900 hover:bg-rose-200 dark:border-rose-600 dark:bg-rose-900/60 dark:text-rose-100 dark:hover:bg-rose-900/80",
  e_stop:
    "border-red-400 bg-red-100 text-red-900 hover:bg-red-200 dark:border-red-600 dark:bg-red-900/60 dark:text-red-100 dark:hover:bg-red-900/80",
  unknown:
    "border-slate-400 bg-slate-100 text-slate-800 hover:bg-slate-200 dark:border-slate-600 dark:bg-slate-700/70 dark:text-slate-200 dark:hover:bg-slate-700/90",
};

/** Per-equipment variant of {@link pillClass}: the active state is tinted by
 *  the instrument's current status instead of a single "green". */
export function equipmentPillClass(state: EquipmentState, active: boolean): string {
  const base =
    "flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors";
  return [
    base,
    active
      ? (STATE_ACTIVE_PILL[state] ?? STATE_ACTIVE_PILL.unknown)
      : "border-slate-300 bg-white text-ink hover:border-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:border-slate-500",
  ].join(" ");
}

export type PlatformHealth = "alert" | "warn" | "ok" | "none";

// Worst-first health rollup for a pill's status dot.
export function platformHealth(snapshots: EquipmentSnapshot[]): PlatformHealth {
  if (snapshots.length === 0) return "none";
  let warn = false;
  for (const s of snapshots) {
    const state = s.status.equipment_status;
    if (s.fetch_error || state === "error" || state === "e_stop") return "alert";
    if (state === "degraded" || state === "requires_init" || state === "unknown") warn = true;
  }
  return warn ? "warn" : "ok";
}

export const HEALTH_DOT: Record<PlatformHealth, string> = {
  alert: "bg-rose-500",
  warn: "bg-amber-400",
  ok: "bg-emerald-500",
  none: "bg-slate-400",
};

/**
 * Classes for a page's filter-pill row so it stays put while the tiles
 * scroll under it.
 *
 * `sticky top-0` is relative to the nearest scrolling ancestor, which the
 * app shell (app/layout.tsx) makes `<main>` — so the row pins to the top of
 * the content region and needs no knowledge of the chrome's height above it
 * (the auth banner renders into a shadow root, so that height isn't knowable
 * at build time anyway).
 *
 * The negative margins + matching padding let the opaque background span the
 * full content column, so tiles don't show through at the edges as they pass
 * beneath. `z-30` clears page content while staying under the fixed overlays
 * (State Reference z-40, assistant z-50). Callers add their own `role` /
 * `aria-label`.
 */
export const stickyPillRowBase =
  "sticky top-0 z-30 -mx-4 bg-surface-subtle px-4 py-3 dark:bg-[#0b1120] sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8";

/** The common case: {@link stickyPillRowBase} plus the standard pill flexbox.
 *  Rows needing a different gap compose the base themselves rather than
 *  appending a conflicting `gap-*` (two gap utilities in one class list
 *  resolve by stylesheet order, not by which was written last). */
export const stickyPillRow = `${stickyPillRowBase} flex flex-wrap items-center gap-1.5`;
