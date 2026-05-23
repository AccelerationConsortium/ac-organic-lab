"use client";

import { useEffect, useState, useTransition } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { postSashMove, postSashStop } from "@/lib/api";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { PositionPill, TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

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
    <TileShell
      snapshot={snapshot}
      headerRight={
        <>
          <LockButton locked={locked} countdown={countdown} onToggle={toggle} noun="sash" />
          <StatusPill state={snapshot.status.equipment_status} />
        </>
      }
    >
      {/* Position pills: LOW (1) on left, HIGH (5) on right */}
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-1">
          {POSITIONS.map((p) => (
            <PositionPill
              key={p}
              label={p}
              isCurrent={sash.position === p && !sash.isMoving}
              isMoving={displayTarget === p && sash.isMoving}
              disabled={locked}
              onClick={() => handleMove(p)}
              ariaLabel={`Move sash to position ${p}`}
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
        <div className="self-start">
          <TileButton onClick={handleStop} disabled={locked} variant="danger">
            Stop
          </TileButton>
        </div>
      )}
    </TileShell>
  );
}
