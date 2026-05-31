"use client";

import { useState, useTransition } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import {
  postPlateIn,
  postPlateOut,
  postPressDown,
  postPressInit,
  postPressStop,
  postPressUp,
} from "@/lib/api";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { PositionPill, TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

interface PressState {
  pressState: string; // "up" | "down" | "unknown"
  plateState: string; // "in" | "out" | "unknown"
}

function parsePress(snapshot: EquipmentSnapshot): PressState {
  const components = snapshot.status.components ?? {};
  const details = (snapshot.status.details ?? {}) as Record<string, unknown>;
  // Prefer the component state (authoritative); fall back to details for
  // legacy responses.
  const pressState =
    components["press_valve"]?.state ??
    (typeof details["press_state"] === "string"
      ? (details["press_state"] as string)
      : "unknown");
  const plateState =
    components["plate"]?.state ??
    (typeof details["plate_state"] === "string"
      ? (details["plate_state"] as string)
      : "unknown");
  return { pressState, plateState };
}

// hold_time on /control/press/{up,down} is clamped 0..10 s by the device.
const HOLD_MIN = 0;
const HOLD_MAX = 10;
const HOLD_STEP = 0.5;
const UP_HOLD_DEFAULT = 2.0;
const DOWN_HOLD_DEFAULT = 5.0;

function clampHold(raw: number): number {
  if (!Number.isFinite(raw)) return 0;
  return Math.min(HOLD_MAX, Math.max(HOLD_MIN, raw));
}

export function PressTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const press = parsePress(snapshot);
  const [, startTransition] = useTransition();
  const { locked, countdown, toggle } = useControlLock();
  const [upHold, setUpHold] = useState<number>(UP_HOLD_DEFAULT);
  const [downHold, setDownHold] = useState<number>(DOWN_HOLD_DEFAULT);

  const status = snapshot.status.equipment_status;
  const isBusy = status === "busy";
  const isReady = status === "ready";
  const isRequiresInit = status === "requires_init";

  // Position pills are click-to-move; lock + busy + requires_init all
  // disable them. "Stop" is the only action allowed while busy.
  const movementDisabled = locked || isBusy || isRequiresInit;

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
            noun="press"
          />
          <StatusPill state={status} />
        </>
      }
    >
      {/* Press + Plate rows: caption + two toggle pills each */}
      <div className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="w-12 shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
            Press
          </span>
          <PositionPill
            label="UP"
            isCurrent={press.pressState === "up"}
            isMoving={isBusy && press.pressState !== "up"}
            disabled={movementDisabled || press.pressState === "up"}
            onClick={() => exec(() => postPressUp(snapshot.id, upHold))}
          />
          <label className="flex items-center gap-1 text-[10px] text-ink-subtle dark:text-slate-500">
            <input
              type="number"
              min={HOLD_MIN}
              max={HOLD_MAX}
              step={HOLD_STEP}
              value={upHold}
              onChange={(e) => setUpHold(clampHold(parseFloat(e.target.value)))}
              disabled={locked || isBusy}
              aria-label="UP hold time in seconds"
              className="h-7 w-12 rounded border border-ink-subtle/40 bg-transparent px-1 text-right text-xs text-ink dark:border-slate-600 dark:text-slate-200 disabled:opacity-50"
            />
            s
          </label>
          <PositionPill
            label="DOWN"
            isCurrent={press.pressState === "down"}
            isMoving={isBusy && press.pressState !== "down"}
            disabled={movementDisabled || press.pressState === "down"}
            onClick={() => exec(() => postPressDown(snapshot.id, downHold))}
          />
          <label className="flex items-center gap-1 text-[10px] text-ink-subtle dark:text-slate-500">
            <input
              type="number"
              min={HOLD_MIN}
              max={HOLD_MAX}
              step={HOLD_STEP}
              value={downHold}
              onChange={(e) => setDownHold(clampHold(parseFloat(e.target.value)))}
              disabled={locked || isBusy}
              aria-label="DOWN hold time in seconds"
              className="h-7 w-12 rounded border border-ink-subtle/40 bg-transparent px-1 text-right text-xs text-ink dark:border-slate-600 dark:text-slate-200 disabled:opacity-50"
            />
            s
          </label>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-12 shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
            Plate
          </span>
          <PositionPill
            label="IN"
            isCurrent={press.plateState === "in"}
            isMoving={isBusy && press.plateState !== "in"}
            disabled={movementDisabled || press.plateState === "in"}
            onClick={() => exec(() => postPlateIn(snapshot.id))}
          />
          <PositionPill
            label="OUT"
            isCurrent={press.plateState === "out"}
            isMoving={isBusy && press.plateState !== "out"}
            disabled={movementDisabled || press.plateState === "out"}
            onClick={() => exec(() => postPlateOut(snapshot.id))}
          />
        </div>
      </div>

      {/* State-aware action buttons */}
      <div className="flex flex-wrap items-center gap-1">
        {isRequiresInit && (
          <TileButton
            onClick={() => exec(() => postPressInit(snapshot.id))}
            disabled={locked}
            variant="primary"
          >
            Init
          </TileButton>
        )}
        {/* Stop is always visible — an abort control should never disappear,
            including before init and after a move completes. Only the control
            lock gates it. */}
        <TileButton
          onClick={() => exec(() => postPressStop(snapshot.id))}
          disabled={locked}
          variant="danger"
        >
          Stop
        </TileButton>
      </div>
    </TileShell>
  );
}
