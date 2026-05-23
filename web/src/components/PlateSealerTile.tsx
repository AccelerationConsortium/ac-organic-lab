"use client";

import {
  useEffect,
  useState,
  useTransition,
  type FormEvent,
} from "react";
import type { EquipmentSnapshot } from "@/types/api";
import {
  postSealerSealStart,
  postSealerSealStop,
  postSealerSetTemperature,
  postSealerSetTime,
  postSealerShutdown,
  postSealerStageIn,
  postSealerStageOut,
  postSealerStartup,
} from "@/lib/api";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

// Validation ranges mirror skills/.../skill_catalog/plate_sealer.py.
const TEMP_MIN = 20;
const TEMP_MAX = 235;
const TIME_MIN = 0.5;
const TIME_MAX = 12.0;

// Heater state from device's components.heater (added by the device service;
// gracefully absent on older deployments).
type HeaterState =
  | "stable"
  | "heating"
  | "cooling"
  | "unknown"
  | "disconnected";

interface SealerState {
  actualTempC: number | null;
  setpointTempC: number | null;
  sealingTimeS: number | null;
  cycleCount: number | null;
  // Heater fields: null when the device hasn't published them yet (old service).
  heaterState: HeaterState | null;
  heaterMessage: string | null;
}

function parseSealer(snapshot: EquipmentSnapshot): SealerState {
  const metrics = snapshot.status.metrics ?? {};
  const components = snapshot.status.components ?? {};
  const num = (key: string): number | null => {
    const v = metrics[key]?.value;
    return typeof v === "number" ? v : null;
  };
  const heater = components["heater"];
  const heaterState =
    heater && typeof heater.state === "string"
      ? (heater.state as HeaterState)
      : null;
  return {
    actualTempC: num("actual_temperature"),
    setpointTempC: num("setpoint_temperature"),
    sealingTimeS: num("sealing_time"),
    cycleCount: num("cycle_count"),
    heaterState,
    heaterMessage: heater?.message ?? null,
  };
}

// Map heater.state -> visual tone applied to the Actual pill and a
// short label appended to its caption ("Actual · heating", etc.).
type Tone = "neutral" | "ok" | "warn" | "muted";

const TONE_CLASSES: Record<Tone, string> = {
  neutral:
    "border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/40",
  ok: "border-emerald-300 bg-emerald-50 dark:border-emerald-700 dark:bg-emerald-950/40",
  warn: "border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/40",
  muted:
    "border-slate-200 bg-slate-100 dark:border-slate-700 dark:bg-slate-800/20",
};

function heaterTone(state: HeaterState | null): Tone {
  if (state === "stable") return "ok";
  if (state === "heating" || state === "cooling") return "warn";
  if (state === "unknown" || state === "disconnected") return "muted";
  return "neutral"; // heater field absent on the device
}

function fmt(value: number | null, unit: string, decimals: number): string {
  if (value === null || value === undefined) return `— ${unit}`;
  return `${value.toFixed(decimals)} ${unit}`;
}

/** Single-line read-only pill: caption left, value right. `tone` tints
 *  the border + background (used for heater state on the Actual pill);
 *  `title` becomes the native hover tooltip (heater.message). */
function MetricPill({
  caption,
  value,
  tone = "neutral",
  title,
}: {
  caption: string;
  value: string;
  tone?: Tone;
  title?: string;
}) {
  return (
    <div
      className={`flex h-7 items-center gap-1 rounded-md border px-2 ${TONE_CLASSES[tone]}`}
      title={title}
    >
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
        {caption}
      </span>
      <span className="ml-auto font-mono text-xs font-semibold text-ink dark:text-slate-100 tabular-nums">
        {value}
      </span>
    </div>
  );
}

/** Single-line editable pill: caption + input + unit + Set, all inline. */
function EditablePill({
  caption,
  current,
  unit,
  step,
  min,
  max,
  decimals,
  locked,
  busy,
  onSet,
}: {
  caption: string;
  current: number | null;
  unit: string;
  step: number;
  min: number;
  max: number;
  decimals: number;
  locked: boolean;
  busy: boolean;
  onSet: (value: number) => void;
}) {
  const [draft, setDraft] = useState<string>(current?.toFixed(decimals) ?? "");

  // When the device-reported value changes (and the user isn't editing),
  // sync the draft.
  useEffect(() => {
    setDraft(current?.toFixed(decimals) ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (locked || busy) return;
    const parsed = parseFloat(draft);
    if (Number.isNaN(parsed)) return;
    if (parsed < min || parsed > max) return;
    onSet(parsed);
  }

  const disabled = locked || busy;
  return (
    <form
      onSubmit={handleSubmit}
      className="flex h-7 items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 dark:border-slate-700 dark:bg-slate-800/40"
    >
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
        {caption}
      </span>
      <input
        type="number"
        inputMode="decimal"
        step={step}
        min={min}
        max={max}
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        className="ml-auto w-12 min-w-0 rounded border border-slate-200 bg-white px-1 py-0 text-right font-mono text-xs tabular-nums text-ink outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-300 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
      />
      <span className="shrink-0 text-[10px] text-ink-subtle dark:text-slate-500">
        {unit}
      </span>
      <TileButton type="submit" size="small" variant="primary" disabled={disabled}>
        Set
      </TileButton>
    </form>
  );
}

export function PlateSealerTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const sealer = parseSealer(snapshot);
  const [, startTransition] = useTransition();
  const { locked, countdown, toggle } = useControlLock();

  const status = snapshot.status.equipment_status;
  const isBusy = status === "busy";
  const isReady = status === "ready";
  const isRequiresInit = status === "requires_init";

  // For inputs and setpoint actions we use the lock; busy doesn't disable
  // setpoint changes (the device accepts them between cycles, and the
  // server's claim semantics will reject if it doesn't).
  const controlsDisabled = locked;

  // Seal start interlock: when the device reports a heater state, only
  // allow Seal start while the heater is stable. If the device hasn't
  // published components.heater (old service), heaterState is null and
  // we fall back to the previous "lock-only" gating.
  const heaterPresent = sealer.heaterState !== null;
  const heaterStable = sealer.heaterState === "stable";
  const sealStartBlockedByHeater = heaterPresent && !heaterStable;
  const sealStartTitle = sealStartBlockedByHeater
    ? sealer.heaterMessage ?? "Waiting for heater to reach setpoint"
    : undefined;

  function exec<T>(fn: () => Promise<T>) {
    startTransition(() => {
      fn().catch(() => {
        /* fail silently; next /status poll will reflect reality */
      });
    });
  }

  return (
    <TileShell
      snapshot={snapshot}
      headerRight={
        <>
          <LockButton
            locked={locked}
            countdown={countdown}
            onToggle={toggle}
            noun="sealer"
          />
          <StatusPill state={status} />
        </>
      }
    >
      {/* 2x2 metric grid. The Actual pill is tinted by the heater state
          (emerald=stable, amber=heating/cooling, slate=unknown/disconnected,
          neutral when the device hasn't published components.heater). */}
      <div className="grid grid-cols-2 gap-1.5">
        <MetricPill
          caption="Actual"
          value={fmt(sealer.actualTempC, "°C", 0)}
          tone={heaterTone(sealer.heaterState)}
          title={sealer.heaterMessage ?? undefined}
        />
        <EditablePill
          caption="Setpoint"
          current={sealer.setpointTempC}
          unit="°C"
          step={1}
          min={TEMP_MIN}
          max={TEMP_MAX}
          decimals={0}
          locked={locked}
          busy={isBusy}
          onSet={(v) => exec(() => postSealerSetTemperature(snapshot.id, v))}
        />
        <EditablePill
          caption="Seal time"
          current={sealer.sealingTimeS}
          unit="s"
          step={0.1}
          min={TIME_MIN}
          max={TIME_MAX}
          decimals={1}
          locked={locked}
          busy={isBusy}
          onSet={(v) => exec(() => postSealerSetTime(snapshot.id, v))}
        />
        <MetricPill
          caption="Cycles"
          value={
            sealer.cycleCount !== null
              ? sealer.cycleCount.toLocaleString()
              : "—"
          }
        />
      </div>

      {/* Action buttons. Visibility is state-aware so the row doesn't
          balloon during requires_init or busy. */}
      <div className="flex flex-wrap items-center gap-1">
        {isRequiresInit && (
          <TileButton
            onClick={() => exec(() => postSealerStartup(snapshot.id))}
            disabled={controlsDisabled}
            variant="primary"
          >
            Startup
          </TileButton>
        )}
        {(isReady || isBusy) && (
          <TileButton
            onClick={() => exec(() => postSealerShutdown(snapshot.id))}
            disabled={controlsDisabled}
          >
            Shutdown
          </TileButton>
        )}
        {isReady && (
          <>
            <TileButton
              onClick={() => exec(() => postSealerStageIn(snapshot.id))}
              disabled={controlsDisabled}
            >
              Stage in
            </TileButton>
            <TileButton
              onClick={() => exec(() => postSealerStageOut(snapshot.id))}
              disabled={controlsDisabled}
            >
              Stage out
            </TileButton>
            <TileButton
              onClick={() => exec(() => postSealerSealStart(snapshot.id))}
              disabled={controlsDisabled || sealStartBlockedByHeater}
              variant="primary"
              title={sealStartTitle}
            >
              Seal start
            </TileButton>
          </>
        )}
        {isBusy && (
          <TileButton
            onClick={() => exec(() => postSealerSealStop(snapshot.id))}
            disabled={controlsDisabled}
            variant="danger"
          >
            Seal stop
          </TileButton>
        )}
      </div>
    </TileShell>
  );
}
