"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * Standardized `last_error` surface for every tile: a small message icon that
 * sits just left of the status pill in the tile header (rendered by TileShell)
 * whenever the device reports a `last_error`. Click it to pop a detail box
 * open; click again (or click away / Escape / scroll) to hide it back to the
 * icon. The box is rendered in a portal so the tile's `overflow-hidden` never
 * clips it, regardless of tile size.
 *
 * Generic by default (shows `code` + raw `message`). A tile can pass an
 * `interpret` fn to enrich the display with device-specific recovery copy
 * (e.g. PlateSealerTile maps plateloc's v1.3.1 code taxonomy to prescriptive
 * sentences). `interpret` returning `null` suppresses the badge entirely.
 */

export interface LastErrorParts {
  /** Device code taxonomy slug (e.g. "low_air_pressure"), or null. */
  code: string | null;
  /** Prescriptive recovery sentence; "" when we have none. */
  recovery: string;
  /** The device's verbatim message. */
  raw: string;
}

type ErrorLike =
  | { code?: string | null; message?: string | null; severity?: string | null }
  | null
  | undefined;

export type LastErrorInterpret = (error: ErrorLike) => LastErrorParts | null;

function defaultInterpret(error: ErrorLike): LastErrorParts | null {
  const raw = (error?.message ?? "").trim();
  if (!raw) return null; // nothing meaningful to show
  return { code: error?.code ?? null, recovery: "", raw };
}

export function LastErrorBadge({
  error,
  interpret,
}: {
  error: ErrorLike;
  interpret?: LastErrorInterpret;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  const parts = error ? (interpret ?? defaultInterpret)(error) : null;

  useEffect(() => {
    if (!open) return;
    const place = () => {
      const r = btnRef.current?.getBoundingClientRect();
      if (r) setPos({ top: r.bottom + 4, right: window.innerWidth - r.right });
    };
    place();
    const close = () => setOpen(false);
    // Reposition would need re-measure; simplest robust UX is to close on
    // scroll/resize so the box never floats detached from its icon.
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

  if (!parts) return null;

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="Device fault details"
        title={open ? "Hide fault" : (parts.code ?? "Device fault")}
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-rose-300 bg-rose-50 text-rose-700 hover:bg-rose-100 dark:border-rose-700 dark:bg-rose-950/40 dark:text-rose-300"
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
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
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
              className="z-50 max-h-64 w-64 overflow-y-auto rounded-md border border-rose-300 bg-rose-50 px-2.5 py-2 text-left text-[11px] leading-snug text-rose-900 shadow-lg dark:border-rose-700 dark:bg-rose-950 dark:text-rose-100"
            >
              {parts.code && (
                <div className="mb-1 font-mono font-semibold">{parts.code}</div>
              )}
              {parts.recovery ? (
                <>
                  {parts.recovery} <span className="opacity-75">{parts.raw}</span>
                </>
              ) : (
                parts.raw
              )}
            </div>
          </>,
          document.body,
        )}
    </>
  );
}
