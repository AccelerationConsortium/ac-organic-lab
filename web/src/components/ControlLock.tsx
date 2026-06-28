"use client";

import type { ReactNode } from "react";

function LockIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="currentColor" aria-hidden>
      <path d="M11 7V5a3 3 0 1 0-6 0v2H4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1h-1Zm-5-2a2 2 0 1 1 4 0v2H6V5Zm2 5a1 1 0 1 1 0 2 1 1 0 0 1 0-2Z" />
    </svg>
  );
}

export interface LockButtonProps {
  /** True when controls are gated (the user is not signed in). */
  locked: boolean;
  /** Vestigial — no countdown anymore; accepted for call-site compatibility. */
  countdown?: number;
  /** Click handler — typically toggle() from useControlLock(), which flashes
   *  the login bar when signed out. */
  onToggle: () => void;
  /** Optional aria noun, e.g. "outlet" -> "Sign in to use outlet controls". */
  noun?: string;
}

/**
 * Header chip for control tiles. With the move to user login, controls are
 * enabled whenever the user is signed in, so when unlocked this renders
 * nothing. When signed out it shows a subtle "Sign in" affordance that points
 * the operator at the sticky login bar.
 */
export function LockButton({
  locked,
  onToggle,
  noun = "control",
}: LockButtonProps): ReactNode {
  if (!locked) return null;

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={`Sign in to use ${noun} controls`}
      title="Sign in to control"
      className={[
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold",
        "ring-1 ring-inset transition-colors",
        "bg-slate-100 text-ink-muted ring-slate-300 hover:bg-slate-200",
        "dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700 dark:hover:bg-slate-700",
      ].join(" ")}
    >
      <LockIcon className="h-3 w-3 shrink-0" />
      Sign in
    </button>
  );
}
