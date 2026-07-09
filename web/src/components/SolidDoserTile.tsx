"use client";

import { useCallback, useEffect, useState, useTransition } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import {
  getDoserBalanceReading,
  getDoserPlateStatus,
  postDoserCloseLid,
  postDoserDoseMultiple,
  postDoserHome,
  postDoserLowerPlate,
  postDoserOpenLid,
  postDoserRaisePlate,
  postDoserShutdown,
  postDoserStartup,
  postDoserTare,
  type PlateStatusInfo,
} from "@/lib/api";
import { useActionError } from "@/lib/use-action-error";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

const TARGET_MG_DEFAULT = 5.0;

// Client-side grid layouts for the well-selection grid. The grid is a
// recording tool (which wells to dose), so its shape is chosen here rather
// than driven by the device's plate definitions. The on-screen plate footprint
// is fixed; fewer wells simply render larger.
interface PlateLayout {
  key: string;
  label: string;
  rows: number;
  columns: number;
}

const PLATE_LAYOUTS: PlateLayout[] = [
  { key: "96", label: "96-well", rows: 8, columns: 12 },
  { key: "54", label: "54-well", rows: 6, columns: 9 },
  { key: "24", label: "24-well", rows: 4, columns: 6 },
];

function wellName(row: number, column: number): string {
  return `${String.fromCharCode(65 + row)}${column + 1}`;
}

// Build the grid's PlateStatusInfo from a client layout, overlaying the
// device's dosed-well info when a plate is actually set. WellGrid reads
// rows/columns/wells; the remaining fields just satisfy the type.
function layoutToPlateStatus(
  layout: PlateLayout,
  deviceWells: PlateStatusInfo["wells"],
): PlateStatusInfo {
  return {
    name: layout.label,
    rows: layout.rows,
    columns: layout.columns,
    total_wells: layout.rows * layout.columns,
    dosed_wells: 0,
    undosed_wells: layout.rows * layout.columns,
    origin: [0, 0],
    wells: deviceWells,
  };
}

export function SolidDoserTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const [isPending, startTransition] = useTransition();
  const { locked, countdown, toggle } = useControlLock(snapshot.id);
  const [targetMg, setTargetMg] = useState<number>(TARGET_MG_DEFAULT);
  // Error state comes from the shared hook; this tile keeps its own
  // useTransition + pendingAction so each button can show a per-action
  // spinner ("Opening…") and clear it in a `.finally`.
  const { actionError, setActionError, reportError } = useActionError();
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  // Only ever updated by an explicit "Weigh" click — the reading fluctuates
  // with air currents/vibration, so a live-polled number would just be
  // noise. `null` until the operator asks for a fresh read.
  const [massG, setMassG] = useState<number | null>(null);

  // Well selection is a client-side recording of which wells to dose. The grid
  // shape comes from `layoutKey` (a client preset — 96-well default, 54-vial
  // for HPLC), NOT from the device. `plateStatus` is still fetched best-effort
  // so dosed wells can be tinted green once a plate is actually set on-device.
  // Starts empty so the picker shows its "Select plate…" placeholder; the
  // grid itself still falls back to the 96-well layout until a choice is made.
  const [layoutKey, setLayoutKey] = useState<string>("");
  const [plateStatus, setPlateStatus] = useState<PlateStatusInfo | null>(null);
  const [selectedWells, setSelectedWells] = useState<Set<string>>(new Set());
  // Purely a local rendering preference — flips which corner A1 renders in
  // without touching well identity/selection or anything on the device.
  const [a1TopLeft, setA1TopLeft] = useState(true);

  const status = snapshot.status.equipment_status;
  const isReady = status === "ready";
  const isRequiresInit = status === "requires_init";

  const components = snapshot.status.components ?? {};
  const gantry = components["gantry"];

  // Live lid/plate position from the device's details blob, used to tint the
  // matching button green (TileButton "primary"). `lid_open` is authoritative;
  // for the plate lift we read plate_weigher.plate_loaded (true = lowered onto
  // the weigher, false = raised) and only tint when a plate is present at all.
  const details = (snapshot.status.details ?? {}) as Record<string, unknown>;
  const lidOpen = details["lid_open"] === true;
  const plateWeigher = details["plate_weigher"] as
    | Record<string, unknown>
    | undefined;
  const platePresent =
    components["plate"] != null && components["plate"].state !== "absent";
  const plateLoaded = plateWeigher?.["plate_loaded"] === true;

  // The grid always renders a client layout; the device's dosed-well info (if
  // any) is overlaid for green tinting.
  const layout =
    PLATE_LAYOUTS.find((l) => l.key === layoutKey) ?? PLATE_LAYOUTS[0];
  const gridPlate = layoutToPlateStatus(layout, plateStatus?.wells ?? []);

  const refreshPlateStatus = useCallback(() => {
    getDoserPlateStatus(snapshot.id)
      .then((s) => setPlateStatus(s))
      .catch(() => setPlateStatus(null)); // no plate set yet (404) — not an error worth surfacing
  }, [snapshot.id]);

  // Sync the grid to whatever plate is actually set on the device once the
  // system is up (covers page refresh mid-session as well as after our own
  // set-plate/dose calls below).
  useEffect(() => {
    if (isReady || status === "degraded") refreshPlateStatus();
  }, [isReady, status, refreshPlateStatus]);

  function exec<T>(name: string, fn: () => Promise<T>) {
    setActionError(null);
    setPendingAction(name);
    startTransition(() => {
      fn()
        .catch((err: unknown) => reportError(err, name))
        .finally(() => setPendingAction((current) => (current === name ? null : current)));
    });
  }

  function weigh() {
    setActionError(null);
    setPendingAction("weigh");
    startTransition(() => {
      getDoserBalanceReading(snapshot.id)
        .then((reading) => setMassG(reading.mass_g))
        .catch((err: unknown) => reportError(err, "weigh"))
        .finally(() => setPendingAction((current) => (current === "weigh" ? null : current)));
    });
  }

  function handleLayoutChange(key: string) {
    setLayoutKey(key);
    setSelectedWells(new Set());
  }

  function toggleWell(name: string) {
    setSelectedWells((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  }

  function doseSelectedWells() {
    if (selectedWells.size === 0) return;
    const wellTargets: Record<string, number> = {};
    selectedWells.forEach((name) => {
      wellTargets[name] = targetMg;
    });
    exec("dose.selected", () =>
      postDoserDoseMultiple(snapshot.id, wellTargets).then((res) => {
        refreshPlateStatus();
        setSelectedWells(new Set());
        return res;
      }),
    );
  }

  function doseAllWells() {
    // Dose every well in the current layout (no device-side "all" call, since
    // the plate layout is client-chosen).
    const wellTargets: Record<string, number> = {};
    for (let r = 0; r < layout.rows; r++) {
      for (let c = 0; c < layout.columns; c++) {
        wellTargets[wellName(r, c)] = targetMg;
      }
    }
    exec("dose.all", () =>
      postDoserDoseMultiple(snapshot.id, wellTargets).then((res) => {
        refreshPlateStatus();
        setSelectedWells(new Set());
        return res;
      }),
    );
  }

  // A claim conflict / transient fault clears itself once the device is
  // back to a normal idle state, same convention as the other tiles.
  useEffect(() => {
    if (actionError && isReady) setActionError(null);
  }, [actionError, isReady]);

  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      lifecycle={{
        // ON toggles startup/shutdown. No STOP: this device has no motion-halt
        // endpoint, and per the template contract STOP must never alias a
        // shutdown/disconnect.
        isOn: !isRequiresInit,
        onPowerToggle: () =>
          isRequiresInit
            ? exec("startup", () => postDoserStartup(snapshot.id))
            : exec("shutdown", () => postDoserShutdown(snapshot.id)),
        disabled: locked || isPending,
        powerTitle: isRequiresInit
          ? "Device is off — click to initialise from the default config"
          : "Device is on — click for safe shutdown + return home",
      }}
      bannerExtra={
        <>
          <TileButton
            onClick={() => exec("home", () => postDoserHome(snapshot.id))}
            disabled={locked || isPending}
            title="Return all components to home"
          >
            {pendingAction === "home" ? "Homing…" : "HOME"}
          </TileButton>
          <TileButton
            onClick={() => setSelectedWells(new Set())}
            disabled={locked || isPending || selectedWells.size === 0}
            title="Clear the well selection"
          >
            CLEAR
          </TileButton>
        </>
      }
      headerRight={
        <>
          <LockButton
            locked={locked}
            countdown={countdown}
            onToggle={toggle}
            noun="solid doser"
          />
          <StatusPill state={status} />
        </>
      }
    >
      {/* Manual lid/plate single-axis moves (lifecycle INIT/STOP + HOME/CLEAR
          live in the template banner above). The loader's own collision guard
          refuses an unsafe move (e.g. raising the plate while the lid is
          closed). */}
      <div className="flex flex-wrap items-center gap-1">
        <TileButton
          onClick={() => exec("lid.open", () => postDoserOpenLid(snapshot.id))}
          disabled={locked || isPending}
          variant={lidOpen ? "primary" : "default"}
        >
          {pendingAction === "lid.open" ? "Opening…" : "Lid Open"}
        </TileButton>
        <TileButton
          onClick={() => exec("lid.close", () => postDoserCloseLid(snapshot.id))}
          disabled={locked || isPending}
          variant={!lidOpen ? "primary" : "default"}
        >
          {pendingAction === "lid.close" ? "Closing…" : "Lid Close"}
        </TileButton>
        <TileButton
          onClick={() => exec("plate.raise", () => postDoserRaisePlate(snapshot.id))}
          disabled={locked || isPending}
          variant={platePresent && !plateLoaded ? "primary" : "default"}
        >
          {pendingAction === "plate.raise" ? "Raising…" : "Plate Up"}
        </TileButton>
        <TileButton
          onClick={() => exec("plate.lower", () => postDoserLowerPlate(snapshot.id))}
          disabled={locked || isPending}
          variant={platePresent && plateLoaded ? "primary" : "default"}
        >
          {pendingAction === "plate.lower" ? "Lowering…" : "Plate Down"}
        </TileButton>
      </div>

      {/* Row 2b — weigh on demand. Not live-polled: the reading fluctuates
          with air currents/vibration, so a static number that only updates
          when asked for is more useful than a jittery live one. */}
      <div className="flex flex-wrap items-center gap-1">
        <TileButton
          onClick={() => exec("tare", () => postDoserTare(snapshot.id))}
          disabled={locked || isPending}
        >
          {pendingAction === "tare" ? "Taring…" : "Tare"}
        </TileButton>
        <TileButton
          onClick={weigh}
          disabled={locked || isPending}
        >
          {pendingAction === "weigh" ? "Weighing…" : "Weigh"}
        </TileButton>
        <input
          type="text"
          readOnly
          value={massG != null ? `${massG.toFixed(4)} g` : "—"}
          aria-label="Balance reading in grams"
          className="h-7 w-28 rounded border border-ink-subtle/40 bg-transparent px-2 text-right text-xs tabular-nums text-ink dark:border-slate-600 dark:text-slate-200"
        />
      </div>

      {/* Gantry state (read-only, from /status) — styled to match the other
          caption/value rows (10px uppercase caption + xs value). */}
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
          Gantry
        </span>
        <span className="text-xs font-semibold text-ink dark:text-slate-100">
          {gantry?.state ?? "—"}
        </span>
      </div>

      {/* Row 3 — plate layout + well selection + dosing (only meaningful when
          ready). The layout picker (96-well / 54-vial) is client-side and just
          reshapes the recording grid; the on-screen footprint stays fixed so
          fewer wells render larger. */}
      {isReady && (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          {/* PLATE TYPE box (fixed 140px wide, stretches to the grid's height)
              to the left of the grid: A1 orientation toggle on top, plate
              picker below (placeholder until a plate is chosen). */}
          <div className="flex min-h-0 flex-1 items-stretch gap-2">
            <div className="flex w-[140px] shrink-0 flex-col gap-1.5 rounded-md border border-slate-200 bg-slate-100 p-2 dark:border-slate-700 dark:bg-slate-800/60">
              <span className="text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
                Plate Type
              </span>
              <TileButton
                onClick={() => setA1TopLeft((v) => !v)}
                disabled={isPending}
              >
                A1: {a1TopLeft ? "Top" : "Bottom"}
              </TileButton>
              <select
                value={layoutKey}
                onChange={(e) => handleLayoutChange(e.target.value)}
                disabled={locked || isPending}
                aria-label="Select plate"
                className="min-w-0 rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-ink disabled:bg-slate-50 disabled:text-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:disabled:bg-slate-900 dark:disabled:text-slate-600"
              >
                <option value="" disabled>
                  Select plate…
                </option>
                {PLATE_LAYOUTS.map((l) => (
                  <option key={l.key} value={l.key}>
                    {l.label}
                  </option>
                ))}
              </select>
            </div>
            <WellGrid
              plateStatus={gridPlate}
              selectedWells={selectedWells}
              a1TopLeft={a1TopLeft}
              disabled={locked || isPending}
              onToggleWell={toggleWell}
            />
          </div>

          <div className="mt-auto flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1 text-[10px] text-ink-subtle dark:text-slate-500">
              <input
                type="number"
                min="0.1"
                step="0.1"
                value={targetMg}
                onChange={(e) => setTargetMg(parseFloat(e.target.value))}
                disabled={locked || isPending}
                aria-label="Target mass per well in milligrams"
                className="h-7 w-16 rounded border border-ink-subtle/40 bg-transparent px-1 text-right text-xs text-ink dark:border-slate-600 dark:text-slate-200 disabled:opacity-50"
              />
              mg / well
            </label>
            <TileButton
              onClick={doseSelectedWells}
              disabled={locked || isPending || selectedWells.size === 0}
              variant="primary"
            >
              Dose{selectedWells.size > 0 ? ` (${selectedWells.size})` : ""}
            </TileButton>
            <TileButton
              onClick={doseAllWells}
              disabled={locked || isPending}
              variant="primary"
            >
              Dose All Wells
            </TileButton>
          </div>
        </div>
      )}

    </TileShell>
  );
}

/**
 * Well-selection grid rendered from a plate layout (rows/columns/well list).
 * The caller passes the device's authoritative `plateStatus` once a plate is
 * set, or a default 96-well layout beforehand so wells can still be selected.
 * Clicking a well toggles it in/out of the caller's selection set; `a1TopLeft`
 * only changes which corner A1 renders in (reversing both axes), not well
 * identity.
 */
function WellGrid({
  plateStatus,
  selectedWells,
  a1TopLeft,
  disabled,
  onToggleWell,
}: {
  plateStatus: PlateStatusInfo;
  selectedWells: Set<string>;
  a1TopLeft: boolean;
  disabled: boolean;
  onToggleWell: (name: string) => void;
}) {
  const wellsByName = new Map(plateStatus.wells.map((w) => [w.name, w]));
  const rowOrder = Array.from({ length: plateStatus.rows }, (_, i) => i);
  const colOrder = Array.from({ length: plateStatus.columns }, (_, i) => i);
  if (!a1TopLeft) {
    rowOrder.reverse();
    colOrder.reverse();
  }

  // The cells are tiny unlabelled squares; the two first-column corner wells
  // are annotated in the gutter *outside* the grid so the labels never eat a
  // cell. Names are derived from the plate dims (A1 / H1 for 96-well, A1 / P1
  // for 384-well). Positions follow the a1TopLeft flip: column 1 is on the
  // left when a1TopLeft, otherwise on the right, with the rows reversed.
  const topWell = wellName(0, 0);
  const bottomWell = wellName(plateStatus.rows - 1, 0);
  const sideCls = a1TopLeft ? "left-0" : "right-0";
  const topVCls = a1TopLeft ? "top-0" : "bottom-0";
  const bottomVCls = a1TopLeft ? "bottom-0" : "top-0";
  const cornerLabelCls =
    "pointer-events-none absolute font-mono text-[11px] leading-none text-ink-subtle dark:text-slate-500";

  return (
    <div className="flex-1 overflow-auto">
      {/* Content-hugging: the grid is only as wide as its wells (fixed cell
          size), so it never stretches to fill the tile. */}
      <div className="relative w-max px-4">
        <span className={`${cornerLabelCls} ${topVCls} ${sideCls}`}>{topWell}</span>
        <span className={`${cornerLabelCls} ${bottomVCls} ${sideCls}`}>
          {bottomWell}
        </span>
        <div
          className="grid gap-px"
          style={{ gridTemplateColumns: `repeat(${plateStatus.columns}, 1.4rem)` }}
        >
          {rowOrder.flatMap((row) =>
            colOrder.map((col) => {
              const name = wellName(row, col);
              const well = wellsByName.get(name);
              const selected = selectedWells.has(name);
              return (
                <button
                  key={name}
                  type="button"
                  onClick={() => onToggleWell(name)}
                  disabled={disabled}
                  title={
                    well
                      ? `${name}${well.dosed ? " — dosed" : ""}${
                          well.target_mass_mg != null
                            ? ` (target ${well.target_mass_mg} mg)`
                            : ""
                        }`
                      : name
                  }
                  className={wellButtonClass(selected, well?.dosed)}
                />
              );
            }),
          )}
        </div>
      </div>
    </div>
  );
}

function wellButtonClass(selected: boolean, dosed?: boolean): string {
  const base =
    "aspect-square rounded-[1px] border transition-colors disabled:opacity-40";
  if (selected) {
    return `${base} border-sky-600 bg-sky-600 text-white dark:border-sky-500 dark:bg-sky-500`;
  }
  if (dosed) {
    return `${base} border-emerald-400 bg-emerald-50 text-emerald-700 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300`;
  }
  return `${base} border-ink-subtle/30 bg-transparent text-ink-muted hover:border-ink-subtle/60 dark:border-slate-700 dark:text-slate-300 dark:hover:border-slate-500`;
}
