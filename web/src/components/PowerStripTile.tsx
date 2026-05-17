"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { kindLabel } from "@/lib/format";
import { postPlugSwitch } from "@/lib/api";
import { StalenessIndicator } from "./StalenessIndicator";
import { StatusPill } from "./StatusPill";

const UNLOCK_DURATION_S = 5;

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
  if (value === null || value === undefined) return `—\u2009${unit}`;
  return `${value.toFixed(decimals)}\u2009${unit}`;
}

interface OutletPillProps {
  outlet: OutletData;
  optimisticOn: boolean | null;
  busy: boolean;
  locked: boolean;
  onToggle: () => void;
}

function OutletPill({ outlet, optimisticOn, busy, locked, onToggle }: OutletPillProps) {
  const isOn = optimisticOn !== null ? optimisticOn : outlet.isOn;
  const hasLoad = outlet.powerW !== null && outlet.powerW > 0.5;
  const disabled = busy || locked;

  return (
    <button
      onClick={onToggle}
      disabled={disabled}
      title={locked ? "Unlock controls to toggle this outlet" : undefined}
      aria-label={`${isOn ? "Turn off" : "Turn on"} ${outlet.label}`}
      className={[
        "flex w-full min-w-0 items-center gap-1.5 rounded-md border px-2 py-1.5 text-left transition-colors",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500",
        locked ? "cursor-not-allowed opacity-40" : "disabled:opacity-50",
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

function LockIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden
    >
      <path d="M11 7V5a3 3 0 1 0-6 0v2H4a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1h-1Zm-5-2a2 2 0 1 1 4 0v2H6V5Zm2 5a1 1 0 1 1 0 2 1 1 0 0 1 0-2Z" />
    </svg>
  );
}

function UnlockIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden
    >
      {/* open shackle on the right, body same as lock */}
      <path d="M11 7H5V5a3 3 0 0 1 5.83-1H12a1 1 0 0 0 0-2h-1.35A5 5 0 0 0 3 5v2H2a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1Zm-4 5a1 1 0 1 1 0-2 1 1 0 0 1 0 2Z" />
    </svg>
  );
}

/** Lock/unlock toggle button shown in the tile header. */
function LockButton({
  locked,
  countdown,
  onToggle,
}: {
  locked: boolean;
  countdown: number;
  onToggle: () => void;
}) {
  if (locked) {
    return (
      <button
        type="button"
        onClick={onToggle}
        aria-label="Unlock outlet controls"
        className={[
          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold",
          "ring-1 ring-inset transition-colors",
          "bg-rose-50 text-rose-700 ring-rose-300",
          "hover:bg-rose-100 dark:bg-rose-950/40 dark:text-rose-300 dark:ring-rose-800 dark:hover:bg-rose-900/60",
        ].join(" ")}
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
      className={[
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold",
        "ring-1 ring-inset transition-colors",
        "bg-amber-50 text-amber-700 ring-amber-300",
        "hover:bg-amber-100 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-700 dark:hover:bg-amber-900/60",
      ].join(" ")}
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

  const [locked, setLocked] = useState(true);
  const [countdown, setCountdown] = useState(UNLOCK_DURATION_S);
  const lockTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function clearLockTimer() {
    if (lockTimerRef.current !== null) {
      clearInterval(lockTimerRef.current);
      lockTimerRef.current = null;
    }
  }

  function lock() {
    clearLockTimer();
    setLocked(true);
    setCountdown(UNLOCK_DURATION_S);
  }

  function unlock() {
    clearLockTimer();
    setCountdown(UNLOCK_DURATION_S);
    setLocked(false);

    let remaining = UNLOCK_DURATION_S;
    lockTimerRef.current = setInterval(() => {
      remaining -= 1;
      setCountdown(remaining);
      if (remaining <= 0) {
        lock();
      }
    }, 1000);
  }

  // Clean up on unmount
  useEffect(() => () => clearLockTimer(), []);

  function handleLockToggle() {
    if (locked) {
      unlock();
    } else {
      lock();
    }
  }

  function handleToggle(outlet: OutletData) {
    if (locked) return;

    const nextOn = !(optimistic[outlet.index] !== undefined
      ? optimistic[outlet.index]
      : outlet.isOn);

    setOptimistic((prev) => ({ ...prev, [outlet.index]: nextOn }));

    startTransition(() => {
      postPlugSwitch(snapshot.id, "toggle", outlet.index).catch(() => {
        setOptimistic((prev) => {
          const next = { ...prev };
          delete next[outlet.index];
          return next;
        });
      });
    });
  }

  const totalW = outlets.reduce((sum, o) => sum + (o.powerW ?? 0), 0);

  return (
    <article className="flex h-full flex-col gap-3 overflow-hidden rounded-xl border border-slate-200 bg-surface-raised p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      {/* Header */}
      <header className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h3 className="text-base font-semibold text-ink dark:text-slate-100">
            {snapshot.name}
          </h3>
          <p className="text-xs text-ink-subtle dark:text-slate-500">
            {kindLabel(snapshot.kind)} · <span className="font-mono">{snapshot.id}</span>
            {totalW > 0 && (
              <span className="ml-2 font-medium text-ink-muted dark:text-slate-300">
                · {totalW.toFixed(1)} W
              </span>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <LockButton locked={locked} countdown={countdown} onToggle={handleLockToggle} />
          <StatusPill state={snapshot.status.equipment_status} />
        </div>
      </header>

      {snapshot.status.message && (
        <p className="text-xs text-ink-muted dark:text-slate-400">
          {snapshot.status.message}
        </p>
      )}

      {/* 2-col × 3-row outlet grid */}
      <div className="grid flex-1 grid-cols-2 gap-1.5 content-start">
        {outlets.map((outlet) => (
          <OutletPill
            key={outlet.index}
            outlet={outlet}
            optimisticOn={optimistic[outlet.index] !== undefined ? optimistic[outlet.index] : null}
            busy={false}
            locked={locked}
            onToggle={() => handleToggle(outlet)}
          />
        ))}
      </div>

      {/* Footer */}
      <footer className="flex items-center justify-between border-t border-slate-100 pt-2 text-xs text-ink-subtle dark:border-slate-800 dark:text-slate-500">
        <span>
          {snapshot.latency_ms != null ? `${snapshot.latency_ms} ms` : "—"}
        </span>
        <StalenessIndicator fetchedAt={snapshot.fetched_at} />
      </footer>
    </article>
  );
}
