import type { EquipmentSnapshot } from "@/types/api";

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
  "sticky top-0 z-30 -mx-4 bg-surface-subtle px-4 py-2 dark:bg-[#0b1120] sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8";

/** The common case: {@link stickyPillRowBase} plus the standard pill flexbox.
 *  Rows needing a different gap compose the base themselves rather than
 *  appending a conflicting `gap-*` (two gap utilities in one class list
 *  resolve by stylesheet order, not by which was written last). */
export const stickyPillRow = `${stickyPillRowBase} flex flex-wrap items-center gap-1.5`;
