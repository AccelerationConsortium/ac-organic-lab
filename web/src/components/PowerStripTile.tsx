"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { postPlugSwitch } from "@/lib/api";
import { useActionError } from "@/lib/use-action-error";
import { useUserAuth } from "@/lib/user-auth";
import { StatusPill } from "./StatusPill";
import { TileShell } from "./TileShell";

// Outlets carry live equipment (hotplates, stirrers); a stray click can kill a
// running experiment. So on top of the dashboard-wide sign-in gate, the strip
// has its own lock: outlets sit under a cover that must be deliberately
// unlocked, and it auto-relocks after this many seconds.
const UNLOCK_SECONDS = 10;

interface OutletData {
  index: number;
  label: string;
  isOn: boolean;
  powerW: number | null;
  currentA: number | null;
  energyKwhToday: number | null;
}

function parseOutlets(snapshot: EquipmentSnapshot): OutletData[] {
  const components = snapshot.status.components ?? {};
  const metrics = snapshot.status.metrics ?? {};

  return Object.entries(components)
    .filter(([key]) => key.startsWith("outlet_"))
    .map(([key, comp]) => {
      const idx = parseInt(key.replace("outlet_", ""), 10);
      return {
        index: idx,
        label: comp.message ?? key,
        isOn: comp.state === "on",
        powerW: (metrics[`power_outlet_${idx}`]?.value as number | null) ?? null,
        currentA: (metrics[`current_outlet_${idx}`]?.value as number | null) ?? null,
        energyKwhToday: (metrics[`energy_kwh_today_outlet_${idx}`]?.value as number | null) ?? null,
      };
    })
    .sort((a, b) => a.index - b.index);
}

function fmt(value: number | null, unit: string, decimals: number): string {
  if (value === null || value === undefined) return `— ${unit}`;
  return `${value.toFixed(decimals)} ${unit}`;
}

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
      <path d="M11 7H5V5a3 3 0 0 1 5.83-1H12a1 1 0 0 0 0-2h-1.35A5 5 0 0 0 3 5v2H2a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1Zm-4 5a1 1 0 1 1 0-2 1 1 0 0 1 0 2Z" />
    </svg>
  );
}

interface OutletPillProps {
  outlet: OutletData;
  optimisticOn: boolean | null;
  disabled: boolean;
  onToggle: () => void;
}

function OutletPill({ outlet, optimisticOn, disabled, onToggle }: OutletPillProps) {
  const isOn = optimisticOn !== null ? optimisticOn : outlet.isOn;
  const hasLoad = outlet.powerW !== null && outlet.powerW > 0.5;

  return (
    <button
      onClick={onToggle}
      disabled={disabled}
      aria-label={`${isOn ? "Turn off" : "Turn on"} ${outlet.label}`}
      className={[
        "flex h-7 w-full min-w-0 items-center gap-1.5 rounded-md border px-2 text-left text-xs font-semibold transition-colors",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500",
        "disabled:opacity-50",
        isOn
          ? "border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/40"
          : "border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/40",
      ].join(" ")}
    >
      {/* Status dot */}
      <span
        className={[
          "h-1.5 w-1.5 shrink-0 rounded-full",
          isOn
            ? hasLoad ? "bg-emerald-500" : "bg-emerald-400"
            : "bg-rose-400",
        ].join(" ")}
      />

      {/* Label */}
      <span className="min-w-0 flex-1 truncate text-xs font-medium leading-none text-ink dark:text-slate-100">
        {outlet.label}
      </span>

      {/* Metrics — shrink-0 so they never get squeezed off */}
      <span className="shrink-0 tabular-nums text-[11px] leading-none text-ink-subtle dark:text-slate-400">
        {fmt(outlet.powerW, "W", 1)}
        <span className="mx-0.5 opacity-40">·</span>
        {fmt(outlet.currentA, "A", 2)}
      </span>
    </button>
  );
}

// Header chip mirroring the lock state. Locked → rose "Locked" (click to
// unlock); unlocked → amber "Unlocked · Ns" (click to re-lock now).
function LockToggle({
  locked,
  countdown,
  onToggle,
}: {
  locked: boolean;
  countdown: number;
  onToggle: () => void;
}) {
  const base =
    "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ring-inset transition-colors";
  if (locked) {
    return (
      <button
        type="button"
        onClick={onToggle}
        aria-label="Unlock outlet controls"
        className={`${base} bg-rose-50 text-rose-700 ring-rose-300 hover:bg-rose-100 dark:bg-rose-950/40 dark:text-rose-300 dark:ring-rose-800 dark:hover:bg-rose-900/60`}
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
      aria-label={`Lock outlet controls (auto-locks in ${countdown}s)`}
      className={`${base} bg-amber-50 text-amber-700 ring-amber-300 hover:bg-amber-100 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-700 dark:hover:bg-amber-900/60`}
    >
      <UnlockIcon className="h-3 w-3 shrink-0" />
      {`Unlocked · ${countdown}s`}
    </button>
  );
}

export function PowerStripTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const outlets = parseOutlets(snapshot);

  const [optimistic, setOptimistic] = useState<Record<number, boolean>>({});
  const [, startTransition] = useTransition();
  const { actionError, reportError } = useActionError();

  const { authenticated, canControl, requestLogin } = useUserAuth();
  const authorized = authenticated && canControl(snapshot.id);

  // Local auto-relocking lock (the accidental-toggle guard). Unlocking requires
  // a signed-in session with a role on this equipment; it then re-locks after
  // UNLOCK_SECONDS.
  const [unlocked, setUnlocked] = useState(false);
  const [countdown, setCountdown] = useState(UNLOCK_SECONDS);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function clearTimer() {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }

  function lock() {
    clearTimer();
    setUnlocked(false);
    setCountdown(UNLOCK_SECONDS);
  }

  function unlock() {
    if (!authenticated) {
      requestLogin();
      return;
    }
    if (!authorized) return; // signed in but no role on this equipment
    clearTimer();
    setUnlocked(true);
    setCountdown(UNLOCK_SECONDS);
    let remaining = UNLOCK_SECONDS;
    timerRef.current = setInterval(() => {
      remaining -= 1;
      setCountdown(remaining);
      if (remaining <= 0) lock();
    }, 1000);
  }

  // Clean up on unmount; re-lock immediately if the session ends mid-window.
  useEffect(() => () => clearTimer(), []);
  useEffect(() => {
    if (!authorized && unlocked) lock();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authorized, unlocked]);

  const locked = !unlocked;

  function handleToggle(outlet: OutletData) {
    if (locked) return;

    const nextOn = !(optimistic[outlet.index] !== undefined
      ? optimistic[outlet.index]
      : outlet.isOn);

    setOptimistic((prev) => ({ ...prev, [outlet.index]: nextOn }));

    startTransition(() => {
      postPlugSwitch(snapshot.id, "toggle", outlet.index).catch((err: unknown) => {
        // Roll back the optimistic flip, then surface the refusal in the band.
        setOptimistic((prev) => {
          const next = { ...prev };
          delete next[outlet.index];
          return next;
        });
        reportError(err, "toggle");
      });
    });
  }

  const totalW = outlets.reduce((sum, o) => sum + (o.powerW ?? 0), 0);

  // Whole-strip power state for the template's ON toggle: on when any outlet
  // (with optimistic overlay) is on. Toggling switches the entire strip.
  const anyOn = outlets.some((o) =>
    optimistic[o.index] !== undefined ? optimistic[o.index] : o.isOn,
  );

  function handleStripToggle() {
    if (locked) return;
    const target = !anyOn;
    // Optimistically flip every outlet, then send the whole-strip switch.
    setOptimistic(Object.fromEntries(outlets.map((o) => [o.index, target])));
    startTransition(() => {
      postPlugSwitch(snapshot.id, target ? "on" : "off").catch((err: unknown) => {
        setOptimistic({});
        reportError(err, target ? "on" : "off");
      });
    });
  }

  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      subtitleExtra={totalW > 0 ? `${totalW.toFixed(1)} W` : undefined}
      lifecycle={{
        // Whole-strip ON/OFF. No STOP (nothing to halt on a plug). Gated by
        // the same unlock window as the per-outlet pills; the off direction
        // additionally confirms (it kills every outlet, incl. hotplates).
        isOn: anyOn,
        onPowerToggle: handleStripToggle,
        confirmOff: "Switch ALL outlets off?",
        disabled: locked,
        powerTitle: locked
          ? "Unlock to control"
          : anyOn
            ? "At least one outlet is on — click to switch the whole strip off"
            : "All outlets off — click to switch the whole strip on",
      }}
      headerRight={
        <>
          <LockToggle
            locked={locked}
            countdown={countdown}
            onToggle={locked ? unlock : lock}
          />
          <StatusPill state={snapshot.status.equipment_status} />
        </>
      }
    >
      {/* 2-col × 3-row outlet grid under a cover. While locked, the cover sits
          over the outlets so a stray click can't flip equipment; click it to
          unlock (requires sign-in) for a UNLOCK_SECONDS window. */}
      <div className="relative flex-1">
        <div className="grid grid-cols-2 gap-1.5 content-start">
          {outlets.map((outlet) => (
            <OutletPill
              key={outlet.index}
              outlet={outlet}
              optimisticOn={optimistic[outlet.index] !== undefined ? optimistic[outlet.index] : null}
              disabled={locked}
              onToggle={() => handleToggle(outlet)}
            />
          ))}
        </div>

        {locked && (
          <button
            type="button"
            onClick={unlock}
            aria-label={
              !authenticated
                ? "Sign in to control"
                : authorized
                  ? "Unlock outlet controls"
                  : "No access to this equipment"
            }
            className="absolute inset-0 z-10 flex items-center justify-center gap-1.5 rounded-md border border-slate-200/80 bg-slate-50/60 text-xs font-semibold text-ink-muted backdrop-blur-[1px] transition-colors hover:bg-slate-100/70 dark:border-slate-700/80 dark:bg-slate-900/50 dark:text-slate-300 dark:hover:bg-slate-800/60"
          >
            <LockIcon className="h-3.5 w-3.5 shrink-0" />
            {!authenticated
              ? "Sign in to control"
              : authorized
                ? "Unlock to control"
                : "No access"}
          </button>
        )}
      </div>

    </TileShell>
  );
}
