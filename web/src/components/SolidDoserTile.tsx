"use client";

import { useCallback, useEffect, useState, useTransition } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import {
  ApiError,
  getDoserBalanceReading,
  getDoserPlateDefinitions,
  getDoserPlateStatus,
  postDoserCloseLid,
  postDoserDoseAll,
  postDoserDoseMultiple,
  postDoserHome,
  postDoserLowerPlate,
  postDoserOpenLid,
  postDoserRaisePlate,
  postDoserSetPlate,
  postDoserShutdown,
  postDoserStartup,
  postDoserTare,
  type PlateDefinitionInfo,
  type PlateStatusInfo,
} from "@/lib/api";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

const TARGET_MG_DEFAULT = 5.0;

// Friendlier labels for the plate-type picker than the raw definition
// keys — falls back to "<name> (RxC)" for anything not in this map (e.g.
// a custom definition added on the device side that the dashboard doesn't
// know about yet).
const PLATE_TYPE_LABELS: Record<string, string> = {
  "96-well-standard": "96-well (Shallow)",
  "96-well-medium": "96-well (Medium)",
  "deep-well-96": "96-well (Tall / Deep)",
  "24-vial-rack": "24 Vials",
  "384-well-standard": "384-well (Standard)",
};

function plateTypeLabel(def: PlateDefinitionInfo): string {
  return PLATE_TYPE_LABELS[def.key] ?? `${def.name} (${def.rows}\u00d7${def.columns})`;
}

function wellName(row: number, column: number): string {
  return `${String.fromCharCode(65 + row)}${column + 1}`;
}

interface ActionError {
  status: number;
  message: string;
}

function interpretActionError(e: unknown): ActionError {
  if (!(e instanceof ApiError)) {
    return { status: 0, message: e instanceof Error ? e.message : String(e) };
  }
  const body = (e.body ?? {}) as Record<string, unknown>;
  const detail = typeof body.detail === "string" ? body.detail : undefined;

  if (e.status === 423) {
    const claimedBy = body.claimed_by as { owner?: string } | undefined;
    const owner = claimedBy?.owner;
    return {
      status: 423,
      message: owner
        ? `Device claim is held by ${owner}. Try again later.`
        : detail ?? "Device is busy with another caller.",
    };
  }

  return { status: e.status, message: detail ?? e.message };
}

export function SolidDoserTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const [isPending, startTransition] = useTransition();
  const { locked, countdown, toggle } = useControlLock(snapshot.id);
  const [targetMg, setTargetMg] = useState<number>(TARGET_MG_DEFAULT);
  const [actionError, setActionError] = useState<ActionError | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  // Only ever updated by an explicit "Weigh" click — the reading fluctuates
  // with air currents/vibration, so a live-polled number would just be
  // noise. `null` until the operator asks for a fresh read.
  const [massG, setMassG] = useState<number | null>(null);

  // Plate/well selection. `plateStatus` is the device's authoritative view
  // (null until a plate has actually been set on the device) — the grid is
  // only rendered from it, not from a locally-guessed preview, so it can
  // never drift out of sync with what /control/plate/set actually applied.
  const [plateDefs, setPlateDefs] = useState<PlateDefinitionInfo[]>([]);
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

  const refreshPlateStatus = useCallback(() => {
    getDoserPlateStatus(snapshot.id)
      .then((s) => setPlateStatus(s))
      .catch(() => setPlateStatus(null)); // no plate set yet (404) — not an error worth surfacing
  }, [snapshot.id]);

  // Plate definitions are static labware metadata (no claim, no
  // initialization required), so fetch once up front to populate the
  // picker regardless of device state.
  useEffect(() => {
    let cancelled = false;
    getDoserPlateDefinitions(snapshot.id)
      .then((defs) => {
        if (!cancelled) setPlateDefs(defs);
      })
      .catch(() => {
        /* dropdown just stays empty; not worth an error band for this */
      });
    return () => {
      cancelled = true;
    };
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
        .catch((err: unknown) => setActionError(interpretActionError(err)))
        .finally(() => setPendingAction((current) => (current === name ? null : current)));
    });
  }

  function weigh() {
    setActionError(null);
    setPendingAction("weigh");
    startTransition(() => {
      getDoserBalanceReading(snapshot.id)
        .then((reading) => setMassG(reading.mass_g))
        .catch((err: unknown) => setActionError(interpretActionError(err)))
        .finally(() => setPendingAction((current) => (current === "weigh" ? null : current)));
    });
  }

  function handlePlateTypeChange(definitionKey: string) {
    if (!definitionKey) return;
    setSelectedWells(new Set());
    exec("plate.set", () =>
      postDoserSetPlate(snapshot.id, definitionKey).then((res) => {
        refreshPlateStatus();
        return res;
      }),
    );
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
    exec("dose.all", () =>
      postDoserDoseAll(snapshot.id, targetMg).then((res) => {
        refreshPlateStatus();
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
      {/* Gantry position (read-only, from /status). */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted dark:text-slate-300">
        <span className="flex items-center gap-1">
          <span className="text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
            Gantry
          </span>
          <span className="font-mono">{gantry?.state ?? "—"}</span>
        </span>
      </div>

      {/* Row 1 — lifecycle */}
      <div className="flex flex-wrap items-center gap-1">
        {isRequiresInit && (
          <TileButton
            onClick={() => exec("startup", () => postDoserStartup(snapshot.id))}
            disabled={locked || isPending}
            variant="primary"
          >
            Init
          </TileButton>
        )}
        <TileButton
          onClick={() => exec("home", () => postDoserHome(snapshot.id))}
          disabled={locked || isPending}
        >
          Home
        </TileButton>
        <TileButton
          onClick={() => exec("shutdown", () => postDoserShutdown(snapshot.id))}
          disabled={locked || isPending}
          variant="danger"
        >
          Shutdown
        </TileButton>
      </div>

      {/* Row 2 — manual lid/plate-lift moves + tare. Granular single-axis
          controls (as opposed to the full load/unload sequences) so an
          operator can drive each axis independently while placing or
          removing a plate. The loader's own collision guard refuses an
          unsafe move (e.g. raising the plate while the lid is closed). */}
      <div className="flex flex-wrap items-center gap-1">
        <TileButton
          onClick={() => exec("lid.open", () => postDoserOpenLid(snapshot.id))}
          disabled={locked || isPending}
        >
          {pendingAction === "lid.open" ? "Opening…" : "Open Lid"}
        </TileButton>
        <TileButton
          onClick={() => exec("lid.close", () => postDoserCloseLid(snapshot.id))}
          disabled={locked || isPending}
        >
          {pendingAction === "lid.close" ? "Closing…" : "Close Lid"}
        </TileButton>
        <TileButton
          onClick={() => exec("plate.raise", () => postDoserRaisePlate(snapshot.id))}
          disabled={locked || isPending}
        >
          {pendingAction === "plate.raise" ? "Raising…" : "Raise Plate"}
        </TileButton>
        <TileButton
          onClick={() => exec("plate.lower", () => postDoserLowerPlate(snapshot.id))}
          disabled={locked || isPending}
        >
          {pendingAction === "plate.lower" ? "Lowering…" : "Lower Plate"}
        </TileButton>
        <TileButton
          onClick={() => exec("tare", () => postDoserTare(snapshot.id))}
          disabled={locked || isPending}
        >
          {pendingAction === "tare" ? "Taring…" : "Tare"}
        </TileButton>
      </div>

      {/* Row 2b — weigh on demand. Not live-polled: the reading fluctuates
          with air currents/vibration, so a static number that only updates
          when asked for is more useful than a jittery live one. */}
      <div className="flex flex-wrap items-center gap-1">
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
          className="h-7 w-28 rounded border border-ink-subtle/40 bg-transparent px-2 text-right font-mono text-xs tabular-nums text-ink dark:border-slate-600 dark:text-slate-200"
        />
      </div>

      {/* Row 3 — plate type + well selection + dosing (only meaningful when
          ready). The grid is rendered strictly from `plateStatus` (the
          device's own view), so it only appears once a plate is actually
          set — no locally-guessed preview that could drift from reality. */}
      {isReady && (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
              Plate
            </span>
            <select
              value={plateStatus?.name ?? ""}
              onChange={(e) => handlePlateTypeChange(e.target.value)}
              disabled={locked || isPending || plateDefs.length === 0}
              className="min-w-0 rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-ink disabled:bg-slate-50 disabled:text-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:disabled:bg-slate-900 dark:disabled:text-slate-600"
            >
              <option value="" disabled>
                {plateDefs.length === 0 ? "(loading…)" : "Select plate type…"}
              </option>
              {plateDefs.map((def) => (
                <option key={def.key} value={def.key}>
                  {plateTypeLabel(def)}
                </option>
              ))}
            </select>
            {plateStatus && (
              <TileButton
                onClick={() => setA1TopLeft((v) => !v)}
                disabled={isPending}
              >
                A1: {a1TopLeft ? "Top-Left" : "Bottom-Right"}
              </TileButton>
            )}
          </div>

          {plateStatus ? (
            <WellGrid
              plateStatus={plateStatus}
              selectedWells={selectedWells}
              a1TopLeft={a1TopLeft}
              disabled={locked || isPending}
              onToggleWell={toggleWell}
            />
          ) : (
            <p className="text-[11px] text-ink-subtle dark:text-slate-500">
              Select a plate type above to load the well grid.
            </p>
          )}

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
              disabled={locked || isPending || !plateStatus}
              variant="primary"
            >
              Dose All Wells
            </TileButton>
          </div>
        </div>
      )}

      {actionError !== null && (
        <div
          role="status"
          className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
        >
          {actionError.status > 0 && (
            <span className="shrink-0 font-mono font-semibold">
              {actionError.status}
            </span>
          )}
          <span className="leading-snug">{actionError.message}</span>
        </div>
      )}
    </TileShell>
  );
}

/**
 * Well-selection grid rendered strictly from the device's own plate status
 * (rows/columns/well list) — never a locally-guessed layout. Clicking a
 * well toggles it in/out of the caller's selection set; `a1TopLeft` only
 * changes which corner A1 renders in (reversing both axes), not well
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

  return (
    <div
      className="grid flex-1 gap-0.5 overflow-auto"
      style={{ gridTemplateColumns: `repeat(${plateStatus.columns}, minmax(1.5rem, 1fr))` }}
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
                      well.target_mass_mg != null ? ` (target ${well.target_mass_mg} mg)` : ""
                    }`
                  : name
              }
              className={wellButtonClass(selected, well?.dosed)}
            >
              {name}
            </button>
          );
        }),
      )}
    </div>
  );
}

function wellButtonClass(selected: boolean, dosed?: boolean): string {
  const base =
    "flex items-center justify-center rounded border py-1 font-mono text-[9px] leading-none transition-colors disabled:opacity-40";
  if (selected) {
    return `${base} border-sky-600 bg-sky-600 text-white dark:border-sky-500 dark:bg-sky-500`;
  }
  if (dosed) {
    return `${base} border-emerald-400 bg-emerald-50 text-emerald-700 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300`;
  }
  return `${base} border-ink-subtle/30 bg-transparent text-ink-muted hover:border-ink-subtle/60 dark:border-slate-700 dark:text-slate-300 dark:hover:border-slate-500`;
}
