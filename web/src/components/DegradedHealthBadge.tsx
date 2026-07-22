"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { ComponentStatus } from "@/types/api";

interface DegradedHealthBadgeProps {
  components?: Record<string, ComponentStatus> | null;
  message?: string | null;
}

export function DegradedHealthBadge({
  components,
  message,
}: DegradedHealthBadgeProps) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const entries = Object.entries(components ?? {});

  useEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = btnRef.current?.getBoundingClientRect();
      if (rect) {
        setPos({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
      }
    };
    place();
    const close = () => setOpen(false);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label="Degraded health details"
        title={open ? "Hide health details" : "Show health details"}
        className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-300 hover:bg-amber-200 dark:bg-amber-950/50 dark:text-amber-300 dark:ring-amber-700 dark:hover:bg-amber-900/60"
      >
        <span aria-hidden>⚠</span>
        Degraded
      </button>
      {open &&
        pos &&
        typeof document !== "undefined" &&
        createPortal(
          <>
            <div
              className="fixed inset-0 z-40"
              onClick={() => setOpen(false)}
              aria-hidden
            />
            <div
              role="status"
              style={{ position: "fixed", top: pos.top, right: pos.right }}
              className="z-50 max-h-64 w-72 overflow-y-auto rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-left text-[11px] leading-snug text-amber-950 shadow-lg dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100"
            >
              <div className="mb-1.5 font-semibold">Hardware health is degraded</div>
              {message && <div className="mb-2">{message}</div>}
              {entries.length > 0 ? (
                <div className="space-y-1.5">
                  {entries.map(([name, component]) => (
                    <div key={name}>
                      <div className="font-mono font-semibold">
                        {name} · {component.state}
                      </div>
                      {component.message && (
                        <div className="opacity-80">{component.message}</div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                !message && <div>No component details were reported.</div>
              )}
            </div>
          </>,
          document.body,
        )}
    </>
  );
}
