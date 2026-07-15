"use client";

import { useState, type ReactNode } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import type { ActionError } from "@/lib/action-error";
import { kindLabel } from "@/lib/format";
import { StalenessIndicator } from "./StalenessIndicator";
import { ActionErrorBadge } from "./ActionErrorBadge";
import { LastErrorBadge, type LastErrorInterpret } from "./LastErrorBadge";
import { TileButton } from "./TileButton";

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
 *      - Left: name (text-sm font-semibold) + subtitle "KIND · id[ · tailscale_ip]"
 *        (text-xs, kind uppercased; IP only when equipment.yaml sets tailscale_ip)
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
  /**
   * The tile's current control-action error (from `useActionError`). Rendered
   * as an amber message icon + popover just left of the status pill — the
   * standardized surface for a refused/failed action (412/423/409/504…) on
   * every tile. Pass it instead of an inline <ActionErrorBand>.
   */
  actionError?: ActionError | null;
  /**
   * Standardized lifecycle top-banner buttons, rendered by the template so
   * every tile's lifecycle row looks identical (semantics modelled on the
   * xArm5 control surface):
   *
   *   - `ON`/`OFF` — a power/connect TOGGLE, rendered like the xArm5's: a
   *     small state dot (glowing emerald when on, grey when off) + the
   *     current state as the label. Green (primary) while on (`isOn`);
   *     clicking calls `onPowerToggle`, which should start the device when
   *     off and shut it down / disconnect when on.
   *   - `STOP` — HALT MOTION (danger). Not a disconnect: wire it to the
   *     device's motion-stop / abort endpoint only. Omit it entirely for
   *     devices that have no halt endpoint rather than aliasing shutdown.
   *
   * A button only renders when its handler is given; `disabled` is the shared
   * gate (`locked || pending`).
   */
  lifecycle?: {
    isOn?: boolean;
    onPowerToggle?: () => void;
    onStop?: () => void;
    disabled?: boolean;
    powerTitle?: string;
    stopTitle?: string;
    /**
     * Optional label overrides for the lifecycle buttons. Default to
     * "ON"/"OFF"/"STOP". Set these when a device's toggle is not literal power
     * — e.g. the OT-2's toggle connects/disconnects the gateway session rather
     * than powering the robot, and its stop is a protocol pause, so it uses
     * CONNECTED/DISCONNECTED/PAUSE. Power-strip tiles (literal outlet power)
     * keep the defaults.
     */
    onLabel?: string;
    offLabel?: string;
    stopLabel?: string;
    /**
     * When set, clicking the toggle in the ON→off direction opens a small
     * confirm popover with this question (e.g. "Switch all outlets off?")
     * before `onPowerToggle` fires — for tiles whose "off" is destructive
     * (same pattern as the xArm5 disconnect confirm). The on direction is
     * never gated.
     */
    confirmOff?: string;
  };
  /**
   * Extra controls placed in the same top banner as INIT / STOP — e.g. a Light
   * toggle on the left, status pills pushed right with `ml-auto`. The banner
   * renders whenever `lifecycle` buttons or `bannerExtra` are present.
   */
  bannerExtra?: ReactNode;
}

export function TileShell({
  snapshot,
  headerRight,
  children,
  footerLeft,
  subtitleExtra,
  lastErrorInterpret,
  actionError = null,
  lifecycle,
  bannerExtra,
}: TileShellProps) {
  const showBanner = Boolean(
    lifecycle?.onPowerToggle || lifecycle?.onStop || bannerExtra,
  );
  // Confirm-before-off popover state (only used when lifecycle.confirmOff is set).
  const [confirmOff, setConfirmOff] = useState(false);

  function handlePowerClick() {
    if (!lifecycle?.onPowerToggle) return;
    if (lifecycle.isOn && lifecycle.confirmOff) {
      setConfirmOff((v) => !v);
      return;
    }
    lifecycle.onPowerToggle();
  }
  const { status } = snapshot;
  const requiredActions = status.required_actions ?? [];
  const hasMessage = Boolean(status.message);
  const hasActions = requiredActions.length > 0;

  // Display-only Tailscale IP from the registry entry (equipment.yaml
  // `tailscale_ip:`). Deliberately NOT derived from base_url — entries whose
  // base_url is a MagicDNS name or a loopback gateway (cameras, plugs) show
  // nothing unless an IP is explicitly configured.
  const address = snapshot.tailscale_ip || null;

  return (
    <article className="flex h-full flex-col gap-2 overflow-hidden rounded-xl border border-slate-200 bg-surface-raised p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      {/* Header */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 className="truncate text-sm font-semibold text-ink dark:text-slate-100">
            {snapshot.name}
          </h3>
          <p className="truncate text-xs text-ink-subtle dark:text-slate-500">
            <span className="uppercase">{kindLabel(snapshot.kind)}</span> ·{" "}
            <span className="font-mono">{snapshot.id}</span>
            {address && (
              <>
                {" "}
                · <span className="font-mono">{address}</span>
              </>
            )}
            {subtitleExtra && (
              <>
                {" "}
                · <span className="font-medium text-ink-muted dark:text-slate-400">{subtitleExtra}</span>
              </>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {/* Standardized badges, left of the status pill: amber for a
              refused/failed control action (this poll's <ActionErrorBadge>),
              rose for a device-reported fault (<LastErrorBadge>). Each renders
              only when it has something to show; click to pop the detail box. */}
          <ActionErrorBadge error={actionError} />
          <LastErrorBadge
            error={status.last_error}
            interpret={lastErrorInterpret}
          />
          {headerRight}
        </div>
      </header>

      {/* Standardized lifecycle top banner: ON power-toggle (green while on)
          + STOP (halt motion, never disconnect) + any tile-specific extras.
          Rendered by the template so the lifecycle row is identical across
          tiles. */}
      {showBanner && (
        <div className="flex items-center gap-2">
          {lifecycle?.onPowerToggle && (
            <div className="relative">
              <TileButton
                onClick={handlePowerClick}
                disabled={lifecycle.disabled}
                variant={lifecycle.isOn ? "primary" : "default"}
                title={lifecycle.powerTitle}
              >
                {/* State dot + ON/OFF label, matching the xArm5 toggle. */}
                <span
                  className={[
                    "mr-1 inline-block h-2 w-2 rounded-full",
                    lifecycle.isOn
                      ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.7)]"
                      : "bg-slate-400",
                  ].join(" ")}
                  aria-hidden
                />
                {lifecycle.isOn
                  ? (lifecycle.onLabel ?? "ON")
                  : (lifecycle.offLabel ?? "OFF")}
              </TileButton>
              {confirmOff && (
                <div className="absolute left-0 top-full z-20 mt-1 w-44 rounded-md border border-slate-200 bg-white p-2 shadow-lg dark:border-slate-700 dark:bg-slate-900">
                  <p className="mb-2 text-xs text-ink dark:text-slate-200">
                    {lifecycle.confirmOff}
                  </p>
                  <div className="flex justify-end gap-1.5">
                    <TileButton size="small" onClick={() => setConfirmOff(false)}>
                      Cancel
                    </TileButton>
                    <TileButton
                      size="small"
                      variant="danger"
                      onClick={() => {
                        setConfirmOff(false);
                        lifecycle.onPowerToggle?.();
                      }}
                    >
                      Yes
                    </TileButton>
                  </div>
                </div>
              )}
            </div>
          )}
          {lifecycle?.onStop && (
            <TileButton
              onClick={lifecycle.onStop}
              disabled={lifecycle.disabled}
              variant="danger"
              title={lifecycle.stopTitle}
            >
              {lifecycle.stopLabel ?? "STOP"}
            </TileButton>
          )}
          {bannerExtra}
        </div>
      )}

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
