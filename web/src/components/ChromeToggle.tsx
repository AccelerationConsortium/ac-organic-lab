"use client";

import { useEffect, useState } from "react";

/**
 * Collapses the dashboard heading (title + logo row) to give the page more
 * vertical room; the nav tabs stay so navigation is never lost. State lives in
 * the `chrome-collapsed` class on <html> (+ localStorage "chrome"), the same
 * pattern as the theme: layout.tsx applies it before first paint, and
 * globals.css hides `.chrome-heading` under it — so a returning user sees no
 * flash of the heading, and this button only toggles the class.
 */
export const CHROME_STORAGE_KEY = "chrome";
export const CHROME_COLLAPSED_CLASS = "chrome-collapsed";

export function ChromeToggle() {
  // SSR renders the expanded glyph; the real state is read on mount. The
  // heading itself never flashes (CSS), only this ~12px glyph may flip once.
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(document.documentElement.classList.contains(CHROME_COLLAPSED_CLASS));
  }, []);

  function toggle() {
    const next = !collapsed;
    document.documentElement.classList.toggle(CHROME_COLLAPSED_CLASS, next);
    try {
      localStorage.setItem(CHROME_STORAGE_KEY, next ? "collapsed" : "expanded");
    } catch {
      /* private mode / storage disabled — the class still applies this visit */
    }
    setCollapsed(next);
  }

  const label = collapsed ? "Show heading" : "Hide heading";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={label}
      aria-expanded={!collapsed}
      title={label}
      className="ml-auto self-center rounded-md p-1.5 text-ink-subtle transition-colors hover:bg-slate-100 hover:text-ink dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-200"
    >
      {/* Chevron: up = "tuck the heading away", down = "bring it back". */}
      <svg
        width="16"
        height="16"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
        className={`transition-transform ${collapsed ? "rotate-180" : ""}`}
      >
        <path d="M3.5 10.25 8 5.75l4.5 4.5" />
      </svg>
    </button>
  );
}
