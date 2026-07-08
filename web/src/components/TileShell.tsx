"use client";

import type { ReactNode } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { kindLabel } from "@/lib/format";
import { StalenessIndicator } from "./StalenessIndicator";
import { LastErrorBadge, type LastErrorInterpret } from "./LastErrorBadge";

// Latency at or above this threshold paints the "_ ms" label amber,
// matching StalenessIndicator's "stale" color. Most devices poll in
// <100 ms; UPLC-MS already runs ~1500 ms so this triggers there as
// a useful warning that the device is borderline against its timeout.
const SLOW_LATENCY_MS = 500;

/**
 * Shared chrome for every control tile.
 *
 * Layout (top → bottom):
 *
 *   1. Header row
 *      - Left: name (text-sm font-semibold) + subtitle "kind · id" (text-[10px])
 *      - Right: `headerRight` slot (typically <LockButton /> + <StatusPill />)
 *
 *   2. Body: `children` (caller-owned content)
 *
 *   3. Footer row (text-[10px] text-ink-subtle)
 *      - Left: status.message + required_actions ("Action needed: …")
 *      - Right: latency_ms + <StalenessIndicator />
 *
 * Padding is p-3 across the board; tile cards are h-full and overflow-hidden
 * so the parent's grid row height controls vertical clipping. If a tile
 * needs more height, bump `tiles.<section>.h` in equipment.yaml.
 */

export interface TileShellProps {
  snapshot: EquipmentSnapshot;
  /** Right-aligned header slot. Almost always <LockButton /> + <StatusPill />. */
  headerRight: ReactNode;
  /** Body content. */
  children: ReactNode;
  /**
   * Override the footer-left text. By default we render `status.message`
   * and any `required_actions` list. Tiles can pass their own string
   * (e.g. a fetch_error variant) but should keep to one or two lines.
   */
  footerLeft?: ReactNode;
  /** Extra inline content in the subtitle line after "kind · id". */
  subtitleExtra?: ReactNode;
  /**
   * Optional enricher for the standardized `last_error` badge (the message
   * icon left of the status pill). Omit for the generic code+message display;
   * a kind-specific tile can map device codes to recovery copy (see
   * PlateSealerTile). Returning null suppresses the badge.
   */
  lastErrorInterpret?: LastErrorInterpret;
}

export function TileShell({
  snapshot,
  headerRight,
  children,
  footerLeft,
  subtitleExtra,
  lastErrorInterpret,
}: TileShellProps) {
  const { status } = snapshot;
  const requiredActions = status.required_actions ?? [];
  const hasMessage = Boolean(status.message);
  const hasActions = requiredActions.length > 0;

  return (
    <article className="flex h-full flex-col gap-2 overflow-hidden rounded-xl border border-slate-200 bg-surface-raised p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      {/* Header */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 className="truncate text-sm font-semibold text-ink dark:text-slate-100">
            {snapshot.name}
          </h3>
          <p className="truncate text-[10px] text-ink-subtle dark:text-slate-500">
            {kindLabel(snapshot.kind)} ·{" "}
            <span className="font-mono">{snapshot.id}</span>
            {subtitleExtra && (
              <>
                {" "}
                · <span className="font-medium text-ink-muted dark:text-slate-400">{subtitleExtra}</span>
              </>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {/* Standardized device-fault badge: a message icon left of the
              status pill, present on every tile whenever the device reports a
              last_error. Click to pop the detail box. */}
          <LastErrorBadge
            error={status.last_error}
            interpret={lastErrorInterpret}
          />
          {headerRight}
        </div>
      </header>

      {/* Body */}
      {children}

      {/* Footer — text-xs + neutral subtle color matches StalenessIndicator.
          Latency goes amber above the slow threshold, same color scheme
          as a "stale" timestamp. */}
      <footer className="mt-auto flex items-end justify-between gap-2 border-t border-slate-100 pt-1 text-xs text-ink-subtle dark:border-slate-800 dark:text-slate-400">
        <div className="min-w-0 flex-1 space-y-0.5">
          {footerLeft ??
            (hasMessage || hasActions ? (
              <>
                {hasMessage && (
                  <div className="truncate" title={status.message ?? undefined}>
                    {status.message}
                  </div>
                )}
                {hasActions && (
                  <div className="truncate">
                    <span className="font-semibold text-amber-700 dark:text-amber-400">
                      Action needed:
                    </span>{" "}
                    <span className="font-mono">{requiredActions.join(", ")}</span>
                  </div>
                )}
              </>
            ) : null)}
        </div>
        <div className="flex shrink-0 items-center gap-2 tabular-nums">
          {snapshot.latency_ms != null && (
            <span
              className={
                snapshot.latency_ms >= SLOW_LATENCY_MS
                  ? "text-amber-700 dark:text-amber-400"
                  : undefined
              }
              title={
                snapshot.latency_ms >= SLOW_LATENCY_MS
                  ? `Slow poll (≥${SLOW_LATENCY_MS} ms)`
                  : undefined
              }
            >
              {snapshot.latency_ms} ms
            </span>
          )}
          <StalenessIndicator fetchedAt={snapshot.fetched_at} />
        </div>
      </footer>
    </article>
  );
}
