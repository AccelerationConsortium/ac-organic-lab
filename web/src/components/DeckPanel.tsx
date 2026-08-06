"use client";

import type { RobotModule, DeviceDeck } from "@/lib/api";
import {
  DECK_ROWS,
  TEMP_FAMILIES,
  buildSlotView,
  computeOverhangReadouts,
  moduleFamily,
  moduleShortLabel,
  pairModuleSlots,
  type SlotView,
  type TipRackSummary,
} from "@/lib/ot2-deck";
import { buildWellModel } from "@/lib/plate-wells";

function formatTemp(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return Number.isInteger(v) ? v.toFixed(0) : v.toFixed(1);
}

// Compact live readout for a temperature-capable module: current temp, target
// (when set), and the module's own status word. Renders gracefully with no
// telemetry (module declared but unpowered / not yet observed): "— °C · offline".
export function ModuleReadout({ live, compact }: { live: RobotModule | null; compact?: boolean }) {
  const cur = live?.current_temperature;
  const tgt = live?.target_temperature;
  const status = live?.status ?? "offline";
  const active = status === "heating" || status === "cooling";
  return (
    <div className="flex min-w-0 flex-col items-center gap-0.5">
      <span
        className={[
          compact ? "text-sm" : "text-xl",
          "font-semibold tabular-nums",
          cur == null ? "text-slate-400 dark:text-slate-500" : "text-ink dark:text-slate-100",
        ].join(" ")}
      >
        {formatTemp(cur)} °C
        {tgt != null && (
          <span className="font-medium text-amber-600 dark:text-amber-400"> → {formatTemp(tgt)} °C</span>
        )}
      </span>
      <span
        className={[
          "text-[9px] uppercase tracking-wider",
          active ? "text-amber-600 dark:text-amber-400" : "text-ink-subtle dark:text-slate-400",
        ].join(" ")}
      >
        {status}
      </span>
    </div>
  );
}

const MINI_ROW_LETTERS = "ABCDEFGHIJKLMNOP";

/**
 * Per-well tint for the deck's miniature grid. A well here is 2–3 px, so this
 * is deliberately *not* the inspector's vocabulary: an outlined "hollow" ring
 * turns to mud at this size, and three tones is already the most a 96-dot
 * thumbnail can carry.
 *
 * - present (default): solid grey, as it has always been.
 * - `empty`: a faint dot — the tip was picked and dropped, the hole is bare.
 * - `touched`: amber, matching the inspector — used, but still in the rack.
 *
 * An **untracked** rack keeps the plain solid grey. That is not a claim it is
 * full; the tile has no honest way to say "unknown" at this scale, so it says
 * nothing, and the slot's tooltip plus the expanded inspector carry the truth.
 */
const MINI_WELL_FILL: Record<string, string> = {
  empty: "bg-slate-100 dark:bg-slate-800",
  touched: "bg-amber-300 dark:bg-amber-600",
};
const MINI_WELL_DEFAULT = "bg-slate-300 dark:bg-slate-600";

// Miniature well grid drawn inside a deck slot once well-plate labware is
// assigned. The inner grid is given the plate's own aspect ratio so every cell
// is square, and it is centred within the (taller) slot box.
export function MiniPlate({
  rows,
  columns,
  wellKinds,
}: {
  rows: number;
  columns: number;
  /** Optional per-well state, keyed `A1`-style. Omitted → every well solid. */
  wellKinds?: Record<string, string>;
}) {
  return (
    <div className="flex h-full w-full items-center justify-center p-1.5">
      <div
        className="grid w-full gap-[2px]"
        style={{
          aspectRatio: `${columns} / ${rows}`,
          gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))`,
        }}
        aria-hidden
      >
        {Array.from({ length: rows * columns }, (_, i) => {
          // CSS grid fills row-major, so index → (row, column) → well id.
          const well = `${MINI_ROW_LETTERS[Math.floor(i / columns)] ?? "?"}${(i % columns) + 1}`;
          const kind = wellKinds?.[well];
          return (
            <span
              key={i}
              className={`rounded-full ${(kind && MINI_WELL_FILL[kind]) || MINI_WELL_DEFAULT}`}
            />
          );
        })}
      </div>
    </div>
  );
}

export interface DeckPanelProps {
  /** The gateway's normalized deck (details.snapshot.deck) — null for an
   *  un-migrated device, in which case `legacyLabware` drives the render. */
  deviceDeck: DeviceDeck | null;
  /** Legacy dashboard-store slots (slot -> kind); ignored when deviceDeck set. */
  legacyLabware?: Record<string, string>;
  /** Live module telemetry (details.robot.modules) for readout pairing. */
  robotModules?: RobotModule[];
  selectedSlot?: number | null;
  /** Omit for a read-only deck (cells render as plain, non-clickable tiles). */
  onSelectSlot?: (slot: number | null) => void;
  /** "tile" = fixed 160×120 cells (dashboard tile); "page" = responsive
   *  full-width cells with labware names rendered inside. */
  variant?: "tile" | "page";
  /** Tip-tracker summaries (`details.tip_racks`). When given, a tip rack's
   *  wells are tinted by real state instead of drawn uniformly full. */
  tipRacks?: TipRackSummary[];
}

/**
 * The 12-slot OT-2 deck (slot 1 bottom-left … 12 top-right, rendered top row
 * first to match the physical deck). Declared vs observed state, mismatch
 * flags, module accent + live temperature readouts (including the
 * temperature-module overhang cell) all come from the shared ot2-deck lib, so
 * the dashboard tile and the full-page interface render identically.
 */
export function DeckPanel({
  deviceDeck,
  legacyLabware = {},
  robotModules = [],
  selectedSlot = null,
  onSelectSlot,
  variant = "tile",
  tipRacks = [],
}: DeckPanelProps) {
  const migrated = deviceDeck != null;
  const page = variant === "page";
  const interactive = onSelectSlot != null;

  const moduleSlots = pairModuleSlots(deviceDeck, robotModules);
  const overhangReadout = computeOverhangReadouts(deviceDeck, moduleSlots);

  /**
   * Per-well state for a tip-rack slot, via the same model the expanded
   * inspector renders — one definition of "fresh / used / empty", so the
   * thumbnail and the detail view can never disagree.
   *
   * Returns undefined (⇒ uniform solid) unless this slot is a tracked tip
   * rack: an untracked rack has no state to show, and inventing one is the
   * failure this exists to avoid.
   */
  function wellKindsFor(v: SlotView): Record<string, string> | undefined {
    if (!v.isTiprack || !v.nickname) return undefined;
    const summary = tipRacks.find((r) => r.nickname === v.nickname);
    if (!summary) return undefined;
    const model = buildWellModel({
      isTiprack: true,
      rows: v.rows,
      columns: v.columns,
      geometry: null,
      tipRack: summary,
      samples: null,
    });
    const out: Record<string, string> = {};
    for (const cell of model.cells) out[cell.well] = cell.kind;
    return out;
  }
  // Module slots whose readout renders in an overhang cell — their own cell
  // then shows only the module name (or the plate sitting on it).
  const exportedReadouts = new Set(
    Array.from(overhangReadout.values(), (o) => o.moduleSlot),
  );

  return (
    <div
      className={
        page
          ? "grid w-full gap-2 sm:gap-3"
          : "grid justify-center gap-[10px] overflow-x-auto"
      }
      style={{ gridTemplateColumns: page ? "repeat(3, minmax(0, 1fr))" : "repeat(3, 160px)" }}
    >
      {DECK_ROWS.flat().map((slot) => {
        const v = buildSlotView(slot, deviceDeck, legacyLabware);
        const selected = selectedSlot === slot;
        const mismatch = v.state === "mismatch";
        // Module presentation: `overhang` puts the paired live readout in
        // this (empty) cell because the long temperature module physically
        // overhangs it from the slot to the right; `paired` is this slot's
        // own module telemetry (shown inline only when no overhang cell
        // exported it and the family reports temperatures).
        const overhang = overhangReadout.get(slot);
        const paired = moduleSlots.get(slot);
        const inlineReadout =
          v.kind === "module" &&
          v.moduleName != null &&
          !exportedReadouts.has(slot) &&
          TEMP_FAMILIES.has(moduleFamily(v.moduleName) ?? "");
        const moduleAccent = overhang != null || v.moduleName != null;
        const cellTitle = overhang
          ? `Slot ${slot} — overhang of the ${overhang.name} at slot ${overhang.moduleSlot}`
          : v.title;
        const cellClassName = [
          "relative overflow-hidden rounded border transition-colors",
          page ? "aspect-[4/3] w-full" : "h-[120px] w-[160px]",
          selected
            ? "border-sky-500 bg-sky-50 dark:border-sky-500 dark:bg-sky-950/40"
            : mismatch
              ? "border-amber-500 bg-amber-50 dark:border-amber-500 dark:bg-amber-950/30"
              : interactive
                ? "border-slate-200 bg-white hover:border-slate-400 dark:border-slate-700 dark:bg-slate-800/40 dark:hover:border-slate-500"
                : "border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800/40",
        ].join(" ");
        const cellBody = (
          <>
            {overhang ? (
              <div className="flex h-full w-full flex-col items-center justify-center gap-1 px-1">
                <span className="text-[9px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
                  {moduleShortLabel(overhang.name)} · slot {overhang.moduleSlot}
                </span>
                <ModuleReadout live={overhang.live} />
              </div>
            ) : v.isTrash ? (
              <div className="flex h-full w-full items-center justify-center bg-slate-300/70 dark:bg-slate-700/60">
                <span className="text-[9px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
                  waste
                </span>
              </div>
            ) : v.rows > 0 && v.columns > 0 ? (
              <MiniPlate rows={v.rows} columns={v.columns} wellKinds={wellKindsFor(v)} />
            ) : v.state !== "empty" ? (
              // Occupied by something without a grid (module, unknown kind).
              <div className="flex h-full w-full flex-col items-center justify-center gap-1 px-1 text-center">
                <span className="text-[10px] font-medium text-ink-subtle dark:text-slate-400">
                  {v.label || v.kind}
                </span>
                {inlineReadout && <ModuleReadout live={paired?.live ?? null} compact />}
              </div>
            ) : (
              <div className="flex h-full w-full items-center justify-center">
                <span className="select-none text-4xl font-semibold text-slate-200 dark:text-slate-700">
                  {slot}
                </span>
              </div>
            )}
            {/* Page variant: name the labware inside the cell (the tile relies
                on tooltips to stay compact). */}
            {page && v.state !== "empty" && !overhang && !v.isTrash && (
              <span
                className="absolute inset-x-0 bottom-0 truncate bg-white/80 px-1 py-0.5 text-left text-[10px] font-medium text-ink dark:bg-slate-900/80 dark:text-slate-200"
                title={v.loadName || v.label}
              >
                {v.label}
              </span>
            )}
            {/* Page variant: slot number pinned top-left on occupied cells. */}
            {page && v.state !== "empty" && (
              <span
                className="absolute left-1 top-1 rounded bg-slate-100/90 px-1 text-[9px] font-semibold text-ink-subtle dark:bg-slate-800/90 dark:text-slate-400"
                aria-hidden
              >
                {slot}
              </span>
            )}
            {/* Amber strip ties the module's cells together: the overhang
                readout cell and the module's own slot (module name or the
                plate sitting on it) read as one 1.5-slot fixture. */}
            {moduleAccent && (
              <span
                className="absolute inset-x-0 top-0 h-[3px] bg-amber-400/90 dark:bg-amber-500/80"
                aria-hidden
              />
            )}
            {/* State badge for migrated devices (top-right corner). The page
                variant also badges declared-only slots so intent vs observed
                is legible at a glance. */}
            {migrated && (v.state === "in_use" || v.state === "mismatch") && (
              <span
                className={[
                  "absolute right-1 top-1 rounded px-1 text-[8px] font-semibold uppercase tracking-wide",
                  v.state === "mismatch" ? "bg-amber-500 text-white" : "bg-sky-500 text-white",
                ].join(" ")}
                aria-hidden
              >
                {v.state === "mismatch" ? "≠" : "busy"}
              </span>
            )}
            {page && migrated && v.state === "declared" && (
              <span
                className="absolute right-1 top-1 rounded border border-dashed border-slate-400 px-1 text-[8px] font-semibold uppercase tracking-wide text-ink-subtle dark:border-slate-500 dark:text-slate-400"
                aria-hidden
              >
                declared
              </span>
            )}
          </>
        );
        return interactive ? (
          <button
            key={slot}
            type="button"
            onClick={() => onSelectSlot?.(selectedSlot === slot ? null : slot)}
            title={cellTitle}
            className={cellClassName}
          >
            {cellBody}
          </button>
        ) : (
          <div key={slot} title={cellTitle} className={cellClassName}>
            {cellBody}
          </div>
        );
      })}
    </div>
  );
}
