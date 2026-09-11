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
 *
 * `zoomAxis` adds a fourth column of optical zoom cells (`zoom_in` /
 * `zoom_out`, same press-and-hold semantics). Only render it when the
 * camera reports `details.has_zoom` — the gateway answers 409 otherwise,
 * and none of the lab's Tapo dual-lens heads has a zoom axis today; the
 * tile's digital zoom (`ZoomControls`) is the view-side substitute.
 */
export function PtzPad({
  onMove,
  onStop,
  disabled = false,
  zoomAxis = false,
}: {
  onMove: (direction: PtzDirection) => void;
  onStop: () => void;
  disabled?: boolean;
  zoomAxis?: boolean;
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

  const ariaLabel = (direction: PtzDirection) =>
    direction === "zoom_in" || direction === "zoom_out"
      ? `Optical ${direction.replace("_", " ")}`
      : `Pan/tilt ${direction.replace("_", " ")}`;

  const cell = (direction: PtzDirection, label: string, gridArea: string) => (
    <button
      type="button"
      key={direction}
      aria-label={ariaLabel(direction)}
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
  // an in-flight move. With a zoom axis, a fourth column carries the
  // optical zoom cells (+ above, − below, a caption between).
  const columns = zoomAxis ? 4 : 3;
  const areas = zoomAxis
    ? '"ul u ur zi" "l c r zl" "dl d dr zo"'
    : '"ul u ur" "l c r" "dl d dr"';
  return (
    <div
      className="grid gap-1"
      style={{
        gridTemplateColumns: `repeat(${columns}, minmax(0, 2.5rem))`,
        gridTemplateRows: "repeat(3, minmax(0, 2.5rem))",
        gridTemplateAreas: areas,
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
      {zoomAxis && (
        <>
          {cell("zoom_in", "+", "zi")}
          <span
            aria-hidden="true"
            style={{ gridArea: "zl" }}
            className="flex items-center justify-center text-[9px] font-semibold uppercase tracking-wider text-ink-muted"
          >
            Zoom
          </span>
          {cell("zoom_out", "−", "zo")}
        </>
      )}
    </div>
  );
}
