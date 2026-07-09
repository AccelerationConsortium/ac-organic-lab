"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { ActionError } from "@/lib/action-error";

/**
 * Standardized action-error surface for every tile: a small amber message icon
 * that sits just left of the status pill in the tile header whenever the last
 * control action was refused or failed (412 precondition / 423 claim conflict /
 * 409 device-state conflict / transport failures like a 504). Click it to pop a
 * detail box open; click again (or click away / Escape / scroll) to hide it.
 * The box is rendered in a portal so the tile's `overflow-hidden` never clips
 * it, regardless of tile size.
 *
 * Amber (not rose) is deliberate — a declined/failed *action*, distinct from a
 * device *fault* (which surfaces in the rose <LastErrorBadge>). This is the
 * counterpart to that badge and shares its geometry. Renders nothing when
 * `error` is null, so TileShell can mount it unconditionally.
 */
export function ActionErrorBadge({ error }: { error: ActionError | null }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const place = () => {
      const r = btnRef.current?.getBoundingClientRect();
      if (r) setPos({ top: r.bottom + 4, right: window.innerWidth - r.right });
    };
    place();
    const close = () => setOpen(false);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Hide the popover automatically once the error clears.
  useEffect(() => {
    if (!error) setOpen(false);
  }, [error]);

  if (!error) return null;

  const tag = error.status > 0 ? String(error.status) : null;

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="Action error details"
        title={open ? "Hide error" : tag ? `Action failed (${tag})` : "Action failed"}
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
      >
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
      </button>
      {open &&
        pos &&
        typeof document !== "undefined" &&
        createPortal(
          <>
            {/* click-away catcher */}
            <div
              className="fixed inset-0 z-40"
              onClick={() => setOpen(false)}
              aria-hidden
            />
            <div
              role="status"
              style={{ position: "fixed", top: pos.top, right: pos.right }}
              className="z-50 max-h-64 w-64 overflow-y-auto rounded-md border border-amber-300 bg-amber-50 px-2.5 py-2 text-left text-[11px] leading-snug text-amber-900 shadow-lg dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100"
            >
              {tag && <span className="mr-1 font-mono font-semibold">{tag}</span>}
              {error.message}
            </div>
          </>,
          document.body,
        )}
    </>
  );
}
