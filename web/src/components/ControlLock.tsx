"use client";

import type { ReactNode } from "react";

function LockIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="currentColor" aria-hidden>
      <path d="M11 7V5a3 3 0 1 0-6 0v2H4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1h-1Zm-5-2a2 2 0 1 1 4 0v2H6V5Zm2 5a1 1 0 1 1 0 2 1 1 0 0 1 0-2Z" />
    </svg>
  );
}

function UnlockIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="currentColor" aria-hidden>
      {/* Open shackle on the right, body same as lock */}
      <path d="M11 7H5V5a3 3 0 0 1 5.83-1H12a1 1 0 0 0 0-2h-1.35A5 5 0 0 0 3 5v2H2a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1Zm-4 5a1 1 0 1 1 0-2 1 1 0 0 1 0 2Z" />
    </svg>
  );
}

export interface LockButtonProps {
  /** Current lock state from useControlLock(). */
  locked: boolean;
  /** Seconds remaining until auto-relock; ignored while locked. */
  countdown: number;
  /** Click handler - typically the toggle() from useControlLock(). */
  onToggle: () => void;
  /** Optional aria/label noun, e.g. "outlet" -> "Lock outlet controls". */
  noun?: string;
}

/**
 * Header chip used by control tiles to toggle the lock guarding their
 * destructive UI. Pair with `useControlLock()` for the state side.
 */
export function LockButton({
  locked,
  countdown,
  onToggle,
  noun = "control",
}: LockButtonProps): ReactNode {
  if (locked) {
    return (
      <button
        type="button"
        onClick={onToggle}
        aria-label={`Unlock ${noun} controls`}
        className={[
          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold",
          "ring-1 ring-inset transition-colors",
          "bg-rose-50 text-rose-700 ring-rose-300",
          "hover:bg-rose-100 dark:bg-rose-950/40 dark:text-rose-300 dark:ring-rose-800 dark:hover:bg-rose-900/60",
        ].join(" ")}
      >
        <LockIcon className="h-3 w-3 shrink-0" />
        Locked
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={`Lock ${noun} controls (auto-locks in ${countdown}s)`}
      className={[
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold",
        "ring-1 ring-inset transition-colors",
        "bg-amber-50 text-amber-700 ring-amber-300",
        "hover:bg-amber-100 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-700 dark:hover:bg-amber-900/60",
      ].join(" ")}
    >
      <UnlockIcon className="h-3 w-3 shrink-0" />
      {`Unlocked · ${countdown}s`}
    </button>
  );
}
