"use client";

import { useState, useTransition } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { kindLabel } from "@/lib/format";
import { postPlugSwitch } from "@/lib/api";
import { useControlLock } from "@/lib/use-control-lock";
import { outletIsSafe } from "@/lib/tile-policy";
import { LockButton } from "./ControlLock";
import { StalenessIndicator } from "./StalenessIndicator";
import { StatusPill } from "./StatusPill";

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

export function PowerStripTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const outlets = parseOutlets(snapshot);

  const [optimistic, setOptimistic] = useState<Record<number, boolean>>({});
  const [, startTransition] = useTransition();

  const { locked, countdown, toggle } = useControlLock();

  function handleToggle(outlet: OutletData) {
    // Light/lamp outlets are convenience controls (see tile-policy.ts);
    // the lock does not apply to them.
    if (locked && !outletIsSafe(outlet.label)) return;

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
          <LockButton locked={locked} countdown={countdown} onToggle={toggle} noun="outlet" />
          <StatusPill state={snapshot.status.equipment_status} />
        </div>
      </header>

      {snapshot.status.message && (
        <p className="text-xs text-ink-muted dark:text-slate-400">
          {snapshot.status.message}
        </p>
      )}

      {/* 2-col × 3-row outlet grid. Light/lamp outlets bypass the lock
          (convenience controls per lib/tile-policy.outletIsSafe). */}
      <div className="grid flex-1 grid-cols-2 gap-1.5 content-start">
        {outlets.map((outlet) => (
          <OutletPill
            key={outlet.index}
            outlet={outlet}
            optimisticOn={optimistic[outlet.index] !== undefined ? optimistic[outlet.index] : null}
            busy={false}
            locked={locked && !outletIsSafe(outlet.label)}
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
