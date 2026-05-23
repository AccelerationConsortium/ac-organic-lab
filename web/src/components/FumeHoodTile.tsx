"use client";

import { useEffect, useState, useTransition } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { kindLabel } from "@/lib/format";
import { postSashMove, postSashStop } from "@/lib/api";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { StalenessIndicator } from "./StalenessIndicator";
import { StatusPill } from "./StatusPill";

const POSITIONS = [1, 2, 3, 4, 5] as const;

type SashState = {
  position: number | null;
  target: number | null;
  isMoving: boolean;
};

function parseSash(snapshot: EquipmentSnapshot): SashState {
  const metrics = snapshot.status.metrics ?? {};
  const rawPos = metrics["sash_position"]?.value;
  const rawTarget = metrics["target_position"]?.value;
  return {
    position: typeof rawPos === "number" ? rawPos : null,
    target: typeof rawTarget === "number" ? rawTarget : null,
    isMoving: snapshot.status.equipment_status === "busy",
  };
}

interface PositionPillProps {
  position: number;
  isCurrent: boolean;
  isTarget: boolean;
  isMoving: boolean;
  locked: boolean;
  busy: boolean;
  onClick: () => void;
}

function PositionPill({
  position,
  isCurrent,
  isTarget,
  isMoving,
  locked,
  busy,
  onClick,
}: PositionPillProps) {
  const disabled = locked || busy;
  const lit = isCurrent && !isMoving;
  const pulsing = isTarget && isMoving;

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={locked ? "Unlock controls to move the sash" : `Move to position ${position}`}
      aria-label={`Move sash to position ${position}`}
      aria-pressed={lit}
      className={[
        "flex h-9 min-w-0 flex-1 items-center justify-center rounded-md border text-sm font-semibold transition-colors",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500",
        locked ? "cursor-not-allowed opacity-40" : "disabled:opacity-50",
        lit
          ? "border-emerald-400 bg-emerald-100 text-emerald-900 dark:border-emerald-600 dark:bg-emerald-900/60 dark:text-emerald-100"
          : pulsing
            ? "animate-pulse border-amber-400 bg-amber-50 text-amber-900 dark:border-amber-600 dark:bg-amber-950/40 dark:text-amber-100"
            : "border-slate-200 bg-slate-50 text-ink-muted dark:border-slate-700 dark:bg-slate-800/40 dark:text-slate-400",
      ].join(" ")}
    >
      {position}
    </button>
  );
}

export function FumeHoodTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const sash = parseSash(snapshot);
  const [optimisticTarget, setOptimisticTarget] = useState<number | null>(null);
  const [, startTransition] = useTransition();

  const { locked, countdown, toggle } = useControlLock();

  // Once the device reports we've arrived at the optimistic target (or the
  // move ends with a different position), clear the optimistic state so the
  // ground truth from /status takes over.
  useEffect(() => {
    if (optimisticTarget === null) return;
    if (!sash.isMoving && sash.position === optimisticTarget) {
      setOptimisticTarget(null);
    }
    if (!sash.isMoving && sash.target === null) {
      setOptimisticTarget(null);
    }
  }, [optimisticTarget, sash.isMoving, sash.position, sash.target]);

  function handleMove(position: number) {
    if (locked) return;
    if (position === sash.position && !sash.isMoving) return;
    setOptimisticTarget(position);
    startTransition(() => {
      postSashMove(snapshot.id, position).catch(() => {
        setOptimisticTarget(null);
      });
    });
  }

  function handleStop() {
    if (locked) return;
    setOptimisticTarget(null);
    startTransition(() => {
      postSashStop(snapshot.id).catch(() => {
        // Best-effort; the next /status poll will reflect reality.
      });
    });
  }

  const displayTarget = optimisticTarget ?? sash.target;
  const showStop = sash.isMoving;

  return (
    <article className="flex h-full flex-col gap-3 overflow-hidden rounded-xl border border-slate-200 bg-surface-raised p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      {/* Header */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h3 className="text-base font-semibold text-ink dark:text-slate-100">
            {snapshot.name}
          </h3>
          <p className="text-xs text-ink-subtle dark:text-slate-500">
            {kindLabel(snapshot.kind)} · <span className="font-mono">{snapshot.id}</span>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <LockButton locked={locked} countdown={countdown} onToggle={toggle} noun="sash" />
          <StatusPill state={snapshot.status.equipment_status} />
        </div>
      </header>

      {snapshot.status.message && (
        <p className="text-xs text-ink-muted dark:text-slate-400">
          {snapshot.status.message}
        </p>
      )}

      {/* Position pills: LOW (1) on left, HIGH (5) on right */}
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-1">
          {POSITIONS.map((p) => (
            <PositionPill
              key={p}
              position={p}
              isCurrent={sash.position === p}
              isTarget={displayTarget === p}
              isMoving={sash.isMoving}
              locked={locked}
              busy={false}
              onClick={() => handleMove(p)}
            />
          ))}
        </div>
        <div className="flex items-center justify-between px-1 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
          <span>Low</span>
          <span>High</span>
        </div>
      </div>

      {/* Stop button — only shown when actually moving */}
      {showStop && (
        <button
          type="button"
          onClick={handleStop}
          disabled={locked}
          className={[
            "self-start rounded-md border px-3 py-1 text-xs font-semibold transition-colors",
            "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500",
            locked
              ? "cursor-not-allowed border-slate-200 bg-slate-50 text-slate-400 opacity-50 dark:border-slate-700 dark:bg-slate-800/40"
              : "border-rose-300 bg-rose-50 text-rose-700 hover:bg-rose-100 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900/60",
          ].join(" ")}
        >
          Stop
        </button>
      )}

      {/* Footer */}
      <footer className="mt-auto flex items-center justify-between border-t border-slate-100 pt-2 text-xs text-ink-subtle dark:border-slate-800 dark:text-slate-500">
        <span>
          {snapshot.latency_ms != null ? `${snapshot.latency_ms} ms` : "—"}
        </span>
        <StalenessIndicator fetchedAt={snapshot.fetched_at} />
      </footer>
    </article>
  );
}
