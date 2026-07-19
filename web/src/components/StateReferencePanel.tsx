"use client";

import { useState } from "react";

import { STATE_COLORS, STATE_META, type StateName } from "@/lib/state-meta";

/**
 * Global State Reference — a click-to-toggle flyout docked to the left edge of
 * the site (mounted once in the root layout).
 *
 * A vertical "State Reference" tag on the panel's right edge is the toggle in
 * both states: click it to slide the legend open, click it again to collapse.
 * Collapsed, only the tag strip peeks out. No hover behaviour — expansion is
 * explicit. Hovering a state row pops its description to the right.
 */
export function StateReferencePanel() {
  const [open, setOpen] = useState(false);

  return (
    <div className="fixed left-0 top-1/2 z-40 -translate-y-1/2 print:hidden">
      <aside
        aria-label="Equipment state reference"
        className={[
          "relative w-44 rounded-r-xl border border-l-0 border-slate-200 bg-surface-raised p-3 pr-8 shadow-lg transition-transform duration-200 dark:border-slate-800 dark:bg-slate-900",
          open ? "translate-x-0" : "-translate-x-[calc(100%-26px)]",
        ].join(" ")}
      >
        {/* Vertical tag on the right edge — the toggle in both states. When
            collapsed it is the only visible part of the panel. */}
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={open ? "Hide state reference" : "Show state reference"}
          title={open ? "Collapse" : "Show state reference"}
          className="absolute right-1.5 top-1/2 -translate-y-1/2 cursor-pointer whitespace-nowrap text-[10px] font-semibold uppercase tracking-widest text-sky-700 hover:text-sky-900 dark:text-sky-400 dark:hover:text-sky-200"
          style={{ writingMode: "vertical-rl" }}
        >
          State Reference
        </button>

        <ul className="flex flex-col gap-1.5">
          {(Object.entries(STATE_META) as [StateName, (typeof STATE_META)[StateName]][]).map(
            ([key, meta]) => (
              <li key={key} className="group/row relative flex cursor-default items-center gap-2">
                <span
                  className="inline-block h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: STATE_COLORS[key] }}
                />
                <span className="text-xs font-medium text-ink dark:text-slate-200">
                  {meta.label}
                </span>
                {/* Description tooltip — pops to the right of the panel. */}
                {open && (
                  <div className="pointer-events-none invisible absolute left-full top-1/2 z-50 ml-4 w-56 -translate-y-1/2 rounded-lg bg-slate-900 px-3 py-2 text-xs leading-relaxed text-white opacity-0 shadow-lg transition-opacity group-hover/row:visible group-hover/row:opacity-100 dark:bg-slate-700">
                    {meta.desc}
                    <span className="absolute left-0 top-1/2 -translate-x-full -translate-y-1/2 border-4 border-transparent border-r-slate-900 dark:border-r-slate-700" />
                  </div>
                )}
              </li>
            ),
          )}
        </ul>
        <p className="mt-3 text-[10px] leading-relaxed text-ink-subtle dark:text-slate-500">
          Hover a label for details.
        </p>
      </aside>
    </div>
  );
}
