"use client";

import { useEffect, useRef, useState } from "react";

export interface PasswordModalProps {
  open: boolean;
  /** Inline error message shown above the input (e.g. "Wrong password"). */
  error: string | null;
  /** Called when the user clicks Submit or hits Enter. */
  onSubmit: (password: string) => void;
  onCancel: () => void;
  busy?: boolean;
}

/**
 * Small modal used by ControlAuthProvider to collect the control password.
 * Intentionally minimal: one input, two buttons, ESC to cancel, Enter to
 * submit. Styling matches the dashboard's card vocabulary.
 */
export function PasswordModal({
  open,
  error,
  onSubmit,
  onCancel,
  busy = false,
}: PasswordModalProps) {
  const [password, setPassword] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setPassword("");
      // focus after the next paint
      const id = window.setTimeout(() => inputRef.current?.focus(), 0);
      return () => window.clearTimeout(id);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="control-auth-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy) onSubmit(password);
        }}
        className="flex w-full max-w-sm flex-col gap-3 rounded-xl border border-slate-200 bg-surface-raised p-5 shadow-lg dark:border-slate-800 dark:bg-slate-900"
      >
        <h2
          id="control-auth-title"
          className="text-base font-semibold text-ink dark:text-slate-100"
        >
          Unlock controls
        </h2>
        <p className="text-xs text-ink-muted dark:text-slate-400">
          Enter the control password to enable destructive actions on this tile.
        </p>

        {error && (
          <p
            role="alert"
            className="rounded-md border border-rose-300 bg-rose-50 px-2 py-1 text-xs text-rose-700 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300"
          >
            {error}
          </p>
        )}

        <input
          ref={inputRef}
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={busy}
          className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm text-ink shadow-inner outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-300 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
        />

        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-md border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-ink-muted hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy || password.length === 0}
            className="rounded-md border border-emerald-400 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-800 hover:bg-emerald-100 disabled:opacity-50 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200 dark:hover:bg-emerald-900/60"
          >
            {busy ? "…" : "Unlock"}
          </button>
        </div>
      </form>
    </div>
  );
}
