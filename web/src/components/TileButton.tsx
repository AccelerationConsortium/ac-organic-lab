"use client";

import type { ReactNode } from "react";

/**
 * Shared button vocabulary for control tiles.
 *
 * Two sizes:
 *   - default: 28 px tall, 12 px text. Tile-row action buttons and
 *     toggle/position pills (Stop, Seal start, UP/DOWN, IN/OUT, etc.).
 *   - small:   20 px tall, 11 px text. Inline Set buttons inside
 *     editable pills, where the parent row is already h-7.
 *
 * Three variants (orthogonal to size):
 *   - default: neutral slate, used for "do this" actions
 *   - primary: emerald, used for "the obvious next step" (e.g. Init when
 *     the device is requires_init, Seal start when idle, Set on a
 *     setpoint input)
 *   - danger:  rose, used for destructive aborts (Stop, Seal stop)
 *
 * Tiles must not invent ad-hoc button styles; if a new variant is
 * needed it belongs here.
 */

export type TileButtonSize = "default" | "small";
export type TileButtonVariant = "default" | "primary" | "danger";

const SIZE_CLASSES: Record<TileButtonSize, string> = {
  default: "h-7 px-2.5 text-xs",
  small: "h-5 px-1.5 text-[11px]",
};

const VARIANT_CLASSES: Record<TileButtonVariant, string> = {
  default:
    "border-slate-200 bg-white text-ink-muted hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700",
  primary:
    "border-emerald-300 bg-emerald-50 text-emerald-800 hover:bg-emerald-100 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200 dark:hover:bg-emerald-900/60",
  danger:
    "border-rose-300 bg-rose-50 text-rose-700 hover:bg-rose-100 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900/60",
};

export interface TileButtonProps {
  onClick?: () => void;
  disabled?: boolean;
  type?: "button" | "submit";
  size?: TileButtonSize;
  variant?: TileButtonVariant;
  /** Used by aria-label callers that want screen-reader detail. */
  ariaLabel?: string;
  title?: string;
  children: ReactNode;
}

export function TileButton({
  onClick,
  disabled = false,
  type = "button",
  size = "default",
  variant = "default",
  ariaLabel,
  title,
  children,
}: TileButtonProps) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      title={title}
      className={[
        "inline-flex shrink-0 items-center justify-center rounded-md border font-semibold transition-colors",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500",
        "disabled:cursor-not-allowed disabled:opacity-40",
        SIZE_CLASSES[size],
        VARIANT_CLASSES[variant],
      ].join(" ")}
    >
      {children}
    </button>
  );
}

/**
 * Toggle pill: a TileButton-shaped element that's visually "lit" when
 * `isCurrent` is true and pulses amber while `isMoving` is true. Used
 * by tiles that expose a finite set of positions (sash 1..5, press
 * UP/DOWN, plate IN/OUT). Click acts as a "move to this position"
 * command; the parent decides what that means.
 */
export interface PositionPillProps {
  label: ReactNode;
  isCurrent: boolean;
  isMoving?: boolean;
  disabled?: boolean;
  onClick: () => void;
  ariaLabel?: string;
}

export function PositionPill({
  label,
  isCurrent,
  isMoving = false,
  disabled = false,
  onClick,
  ariaLabel,
}: PositionPillProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={isCurrent}
      aria-label={ariaLabel}
      className={[
        "flex h-7 min-w-0 flex-1 items-center justify-center rounded-md border text-xs font-semibold transition-colors",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500",
        "disabled:cursor-not-allowed disabled:opacity-40",
        isCurrent
          ? "border-emerald-400 bg-emerald-100 text-emerald-900 dark:border-emerald-600 dark:bg-emerald-900/60 dark:text-emerald-100"
          : isMoving
            ? "animate-pulse border-amber-400 bg-amber-50 text-amber-900 dark:border-amber-600 dark:bg-amber-950/40 dark:text-amber-100"
            : "border-slate-200 bg-slate-50 text-ink-muted dark:border-slate-700 dark:bg-slate-800/40 dark:text-slate-300",
      ].join(" ")}
    >
      {label}
    </button>
  );
}
