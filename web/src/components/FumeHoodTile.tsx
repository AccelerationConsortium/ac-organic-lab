"use client";

import { useEffect, useState } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { postSashMove, postSashStop } from "@/lib/api";
import { useActionError } from "@/lib/use-action-error";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { PositionPill } from "./TileButton";
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
  const { actionError, exec } = useActionError();

  const { locked, countdown, toggle } = useControlLock(snapshot.id);

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
    // On failure the shared band shows the refusal (423/etc.) and we drop the
    // optimistic target so the next /status poll takes over.
    exec(() => postSashMove(snapshot.id, position), {
      action: "sash.move",
      onError: () => setOptimisticTarget(null),
    });
  }

  function handleStop() {
    if (locked) return;
    setOptimisticTarget(null);
    exec(() => postSashStop(snapshot.id), { action: "sash.stop" });
  }

  const displayTarget = optimisticTarget ?? sash.target;

  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      lifecycle={{
        // No power toggle: the actuator has no connect/startup surface. STOP
        // is a true halt (sash/stop).
        onStop: handleStop,
        disabled: locked,
        stopTitle: "Halt sash movement",
      }}
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

    </TileShell>
  );
}
