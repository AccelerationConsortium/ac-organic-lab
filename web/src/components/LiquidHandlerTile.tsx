"use client";

import { useState, useTransition } from "react";

import { ApiError, postSetLights } from "@/lib/api";
import type { EquipmentSnapshot } from "@/types/api";
import { useControlLock } from "@/lib/use-control-lock";
import { useUserAuth } from "@/lib/user-auth";

import { ComponentList } from "./ComponentList";
import { LockButton } from "./ControlLock";
import { MetricList } from "./MetricList";
import { StatusPill } from "./StatusPill";
import { TileShell } from "./TileShell";

type LightsState = "on" | "off" | "unknown";

function parseLights(snapshot: EquipmentSnapshot): LightsState {
  const raw = snapshot.status.components?.["lights"]?.state;
  if (raw === "on" || raw === "off") return raw;
  return "unknown";
}

export function LiquidHandlerTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const { status } = snapshot;
  const metrics = status.metrics ?? {};
  const components = status.components ?? {};
  // Display all components on the tile *except* `lights` — it gets its own
  // dedicated toggle, so listing it twice would be noise.
  const componentsWithoutLights = Object.fromEntries(
    Object.entries(components).filter(([k]) => k !== "lights"),
  );

  const lights = parseLights(snapshot);
  const { locked, noAccess, countdown, toggle } = useControlLock(snapshot.id);
  const [, startTransition] = useTransition();
  const [pending, setPending] = useState<boolean>(false);
  const [actionError, setActionError] = useState<string | null>(null);

  function setLights(on: boolean) {
    setActionError(null);
    setPending(true);
    startTransition(() => {
      postSetLights(snapshot.id, on)
        .catch((e: unknown) => {
          if (e instanceof ApiError) {
            const detail =
              typeof (e.body as { detail?: unknown } | null)?.detail === "string"
                ? ((e.body as { detail: string }).detail)
                : e.message;
            setActionError(`HTTP ${e.status}: ${detail}`);
          } else {
            setActionError(e instanceof Error ? e.message : String(e));
          }
        })
        .finally(() => setPending(false));
    });
  }

  const lightsKnown = lights === "on" || lights === "off";
  const isOn = lights === "on";

  return (
    <TileShell
      snapshot={snapshot}
      headerRight={
        <>
          {/* Lock chip is for the protocol-execution actions (home, setup,
              aspirate, dispense, ...) which will land in a follow-up tile.
              The Lights toggle below is convenience-class: no lock chip, but
              it still requires a signed-in session (disabled when logged
              out), like every control write. */}
          <LockButton
            locked={locked}
            countdown={countdown}
            onToggle={toggle}
            noun="liquid handler"
          />
          <StatusPill state={status.equipment_status} />
        </>
      }
    >
      {/* Lights row — enabled when signed in (no lock chip). */}
      <div className="flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 dark:border-slate-700 dark:bg-slate-800/40">
        <span className="text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
          Lights
        </span>
        <span
          className={[
            "ml-1 inline-block h-2.5 w-2.5 rounded-full",
            isOn
              ? "bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.7)]"
              : lightsKnown
                ? "bg-slate-300 dark:bg-slate-600"
                : "bg-slate-200 dark:bg-slate-700",
          ].join(" ")}
          aria-hidden
          title={
            lightsKnown
              ? isOn
                ? "On"
                : "Off"
              : "Lights state not reported"
          }
        />
        <span className="font-mono text-xs font-semibold text-ink dark:text-slate-100">
          {lightsKnown ? (isOn ? "ON" : "OFF") : "—"}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {locked && (
            <span className="mr-1 text-[10px] text-ink-subtle dark:text-slate-500">
              {noAccess ? "no access" : "sign in to control"}
            </span>
          )}
          <button
            type="button"
            onClick={() => setLights(true)}
            title={locked ? (noAccess ? "No access to this equipment" : "Sign in to control") : undefined}
            disabled={locked || pending || isOn}
            className={[
              "h-7 rounded-md border px-2 text-xs font-semibold transition-colors",
              "disabled:cursor-not-allowed disabled:opacity-50",
              isOn
                ? "border-amber-300 bg-amber-100 text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
                : "border-slate-300 bg-white text-ink hover:bg-slate-100 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700",
            ].join(" ")}
          >
            On
          </button>
          <button
            type="button"
            onClick={() => setLights(false)}
            title={locked ? (noAccess ? "No access to this equipment" : "Sign in to control") : undefined}
            disabled={locked || pending || (lightsKnown && !isOn)}
            className={[
              "h-7 rounded-md border px-2 text-xs font-semibold transition-colors",
              "disabled:cursor-not-allowed disabled:opacity-50",
              lightsKnown && !isOn
                ? "border-slate-300 bg-slate-100 text-ink-muted dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                : "border-slate-300 bg-white text-ink hover:bg-slate-100 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700",
            ].join(" ")}
          >
            Off
          </button>
        </div>
      </div>

      {actionError && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
          {actionError}
        </div>
      )}

      {Object.keys(metrics).length > 0 && <MetricList metrics={metrics} />}
      {Object.keys(componentsWithoutLights).length > 0 && (
        <ComponentList components={componentsWithoutLights} />
      )}

      {snapshot.fetch_error && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
          <div className="font-medium">Aggregator could not reach device</div>
          <div className="font-mono">
            {snapshot.fetch_error.kind}
            {snapshot.fetch_error.http_status
              ? ` · HTTP ${snapshot.fetch_error.http_status}`
              : ""}
          </div>
        </div>
      )}
    </TileShell>
  );
}
