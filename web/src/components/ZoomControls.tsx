"use client";

import { canZoomIn, canZoomOut, formatZoom } from "@/lib/video-zoom";

/**
 * Digital zoom column for the camera tile: `+` / current level (click to
 * reset) / `−`, sized to sit beside the `PtzPad` (same 2.5 rem cells).
 *
 * This is a *view* control — it crops the stream in the browser and never
 * reaches the camera — so, unlike the PTZ pad, it is not gated on the
 * viewer's control role. Optical magnification on the Tapo dual-lens heads
 * comes from switching to the Tele lens, which the tooltip says.
 */
export function ZoomControls({
  level,
  onZoomIn,
  onZoomOut,
  onReset,
  disabled = false,
}: {
  level: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onReset: () => void;
  disabled?: boolean;
}) {
  const cell = (enabled: boolean) =>
    `flex h-10 w-10 items-center justify-center rounded-md border transition-colors ${
      enabled
        ? "border-slate-300 bg-white text-ink hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700"
        : "border-slate-200 bg-slate-100 text-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-600"
    }`;
  const zoomInOk = !disabled && canZoomIn(level);
  const zoomOutOk = !disabled && canZoomOut(level);
  const resetOk = !disabled && level > 1;

  return (
    <div
      role="group"
      aria-label="Digital zoom"
      className="grid gap-1"
      style={{ gridTemplateRows: "repeat(3, minmax(0, 2.5rem))" }}
    >
      <button
        type="button"
        aria-label="Zoom in"
        title="Digital zoom in — crops the stream. Switch to the Tele lens for optical magnification."
        disabled={!zoomInOk}
        onClick={onZoomIn}
        className={`${cell(zoomInOk)} text-base font-medium`}
      >
        +
      </button>
      <button
        type="button"
        aria-label="Reset zoom"
        title={level > 1 ? "Back to 1×" : "Digital zoom level"}
        disabled={!resetOk}
        onClick={onReset}
        className={`${cell(resetOk)} text-[11px] font-semibold tabular-nums`}
      >
        {formatZoom(level)}
      </button>
      <button
        type="button"
        aria-label="Zoom out"
        title="Digital zoom out"
        disabled={!zoomOutOk}
        onClick={onZoomOut}
        className={`${cell(zoomOutOk)} text-base font-medium`}
      >
        −
      </button>
    </div>
  );
}
