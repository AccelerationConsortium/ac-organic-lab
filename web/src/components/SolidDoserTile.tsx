"use client";

import { useState, useTransition } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import {
  postDoserDoseAll,
  postDoserHome,
  postDoserPlateLoad,
  postDoserPlateUnload,
  postDoserShutdown,
  postDoserStartup,
  postDoserTare,
} from "@/lib/api";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

const TARGET_MG_DEFAULT = 5.0;

/**
 * Read the balance reading (mg) off the `balance` component, if the device
 * publishes it. The typed `ComponentStatus` doesn't carry `reading_mg`, so
 * read it defensively as an extra field.
 */
function balanceReadingMg(snapshot: EquipmentSnapshot): number | null {
  const balance = snapshot.status.components?.["balance"] as
    | (Record<string, unknown> & { reading_mg?: unknown })
    | undefined;
  const raw = balance?.reading_mg;
  return typeof raw === "number" ? raw : null;
}

export function SolidDoserTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const [isPending, startTransition] = useTransition();
  const { locked, countdown, toggle } = useControlLock();
  const [targetMg, setTargetMg] = useState<number>(TARGET_MG_DEFAULT);

  const status = snapshot.status.equipment_status;
  const isReady = status === "ready";
  const isRequiresInit = status === "requires_init";

  const components = snapshot.status.components ?? {};
  const gantry = components["gantry"];
  const readingMg = balanceReadingMg(snapshot);

  function exec<T>(fn: () => Promise<T>) {
    startTransition(() => {
      fn().catch(() => {
        /* fail silently; next /status poll catches up */
      });
    });
  }

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
      {/* Read-only state: balance reading + gantry position */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted dark:text-slate-300">
        <span className="flex items-center gap-1">
          <span className="text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
            Balance
          </span>
          <span className="font-mono tabular-nums">
            {readingMg != null ? `${readingMg.toFixed(2)} mg` : "—"}
          </span>
        </span>
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
            onClick={() => exec(() => postDoserStartup(snapshot.id))}
            disabled={locked || isPending}
            variant="primary"
          >
            Init
          </TileButton>
        )}
        <TileButton
          onClick={() => exec(() => postDoserHome(snapshot.id))}
          disabled={locked || isPending}
        >
          Home
        </TileButton>
        <TileButton
          onClick={() => exec(() => postDoserShutdown(snapshot.id))}
          disabled={locked || isPending}
          variant="danger"
        >
          Shutdown
        </TileButton>
      </div>

      {/* Row 2 — plate */}
      <div className="flex flex-wrap items-center gap-1">
        <TileButton
          onClick={() => exec(() => postDoserPlateLoad(snapshot.id))}
          disabled={locked || isPending}
        >
          Load Plate
        </TileButton>
        <TileButton
          onClick={() => exec(() => postDoserPlateUnload(snapshot.id))}
          disabled={locked || isPending}
        >
          Unload Plate
        </TileButton>
        <TileButton
          onClick={() => exec(() => postDoserTare(snapshot.id))}
          disabled={locked || isPending}
        >
          Tare
        </TileButton>
      </div>

      {/* Row 3 — dosing (only meaningful when ready) */}
      {isReady && (
        <div className="flex flex-wrap items-center gap-2">
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
            mg
          </label>
          <TileButton
            onClick={() => exec(() => postDoserDoseAll(snapshot.id, targetMg))}
            disabled={locked || isPending}
            variant="primary"
          >
            Dose All Wells
          </TileButton>
        </div>
      )}
    </TileShell>
  );
}
