"use client";

import type { MouseEvent, PointerEvent } from "react";
import { useCallback, useState } from "react";

import type { PtzDirection } from "@/types/api";

/**
 * 8-direction PTZ pad. Press and hold a button to start a continuous
 * move; release to stop. Tapping (no drag) sends a single short nudge
 * (200ms) so a single tap recenters the camera by a small step rather
 * than not moving at all.
 *
 * We rely on pointer events (works for mouse + touch + pen) and on the
 * pointerCancel/pointerLeave events to trigger the matching `stop` even
 * if the user drags out of the button while still holding.
 */
export function PtzPad({
  onMove,
  onStop,
  disabled = false,
}: {
  onMove: (direction: PtzDirection) => void;
  onStop: () => void;
  disabled?: boolean;
}) {
  const [active, setActive] = useState<PtzDirection | null>(null);

  const start = useCallback(
    (direction: PtzDirection) => (event: PointerEvent<HTMLButtonElement>) => {
      if (disabled) return;
      event.preventDefault();
      (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
      setActive(direction);
      onMove(direction);
    },
    [disabled, onMove],
  );

  const finish = useCallback(
    (event: PointerEvent<HTMLButtonElement> | MouseEvent<HTMLButtonElement>) => {
      if (disabled) return;
      event.preventDefault();
      if (active) {
        setActive(null);
        onStop();
      }
    },
    [active, disabled, onStop],
  );

  const cell = (direction: PtzDirection, label: string, gridArea: string) => (
    <button
      type="button"
      key={direction}
      aria-label={`Pan/tilt ${direction.replace("_", " ")}`}
      onPointerDown={start(direction)}
      onPointerUp={finish}
      onPointerLeave={(e) => {
        if (active === direction) finish(e);
      }}
      onPointerCancel={finish}
      disabled={disabled}
      style={{ gridArea }}
      className={`flex h-10 w-10 items-center justify-center rounded-md border text-sm font-medium transition-colors ${
        disabled
          ? "border-slate-200 bg-slate-100 text-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-600"
          : active === direction
            ? "border-sky-500 bg-sky-100 text-sky-900 dark:border-sky-400 dark:bg-sky-900/40 dark:text-sky-100"
            : "border-slate-300 bg-white text-ink hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700"
      }`}
    >
      {label}
    </button>
  );

  // 3x3 grid; the center cell is a "stop" button so users can interrupt
  // an in-flight move.
  return (
    <div
      className="grid gap-1"
      style={{
        gridTemplateColumns: "repeat(3, minmax(0, 2.5rem))",
        gridTemplateRows: "repeat(3, minmax(0, 2.5rem))",
        gridTemplateAreas: '"ul u ur" "l c r" "dl d dr"',
      }}
    >
      {cell("up_left", "↖", "ul")}
      {cell("up", "↑", "u")}
      {cell("up_right", "↗", "ur")}
      {cell("left", "←", "l")}
      <button
        type="button"
        aria-label="Stop pan/tilt"
        onClick={() => {
          if (disabled) return;
          setActive(null);
          onStop();
        }}
        disabled={disabled}
        style={{ gridArea: "c" }}
        className={`flex h-10 w-10 items-center justify-center rounded-md border text-[10px] font-semibold uppercase tracking-wider transition-colors ${
          disabled
            ? "border-slate-200 bg-slate-100 text-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-600"
            : "border-slate-300 bg-slate-100 text-slate-700 hover:bg-slate-200 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
        }`}
      >
        Stop
      </button>
      {cell("right", "→", "r")}
      {cell("down_left", "↙", "dl")}
      {cell("down", "↓", "d")}
      {cell("down_right", "↘", "dr")}
    </div>
  );
}
