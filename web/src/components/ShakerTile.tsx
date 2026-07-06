"use client";

import { useEffect, useState, useTransition, type FormEvent } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import {
  ApiError,
  postShakerSetTemperature,
  postShakerShakeStart,
  postShakerShakeStop,
  postShakerStartup,
} from "@/lib/api";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

// Ranges mirror skills/.../skill_catalog/shaker.py (and the device's Pydantic
// Field(ge=, le=) constraints).
const TEMP_MIN = -20;
const TEMP_MAX = 110;
const SPEED_MIN = 1;
const SPEED_MAX = 9;
const DURATION_MIN = 1;
// shake.start has no upper bound on duration_s server-side (gt=0); the input
// caps for sanity. Operators who need longer can call the API directly.
const DURATION_MAX = 86_400;

const DEFAULT_TEMP_C = 25;
const DEFAULT_SPEED = 5;
const DEFAULT_DURATION_S = 30;

type HeaterState =
  | "stable"
  | "heating"
  | "cooling"
  | "unknown"
  | "disconnected";

type MotorState = "idle" | "running" | "shaking" | "unknown" | "disconnected";

interface ShakerState {
  actualTempC: number | null;
  speedLevel: number | null;
  heaterState: HeaterState | null;
  heaterMessage: string | null;
  motorState: MotorState | null;
  motorMessage: string | null;
  toleranceC: number | null;
}

function parseShaker(snapshot: EquipmentSnapshot): ShakerState {
  const metrics = snapshot.status.metrics ?? {};
  const components = snapshot.status.components ?? {};
  const details = (snapshot.status.details ?? {}) as Record<string, unknown>;
  const num = (key: string): number | null => {
    const v = metrics[key]?.value;
    return typeof v === "number" ? v : null;
  };
  const heater = components["heater"];
  const motor = components["motor"];
  const rawTol = details["temperature_tolerance_c"];
  return {
    actualTempC: num("actual_temperature"),
    speedLevel: num("speed_level"),
    heaterState:
      heater && typeof heater.state === "string"
        ? (heater.state as HeaterState)
        : null,
    heaterMessage: heater?.message ?? null,
    motorState:
      motor && typeof motor.state === "string"
        ? (motor.state as MotorState)
        : null,
    motorMessage: motor?.message ?? null,
    toleranceC: typeof rawTol === "number" && rawTol > 0 ? rawTol : null,
  };
}

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
  return "neutral";
}

function motorTone(state: MotorState | null, level: number | null): Tone {
  if (state === "running" || state === "shaking") return "warn";
  if (state === "idle") return level && level > 0 ? "warn" : "neutral";
  if (state === "unknown" || state === "disconnected") return "muted";
  return "neutral";
}

function fmt(value: number | null, unit: string, decimals: number): string {
  if (value === null || value === undefined) return `— ${unit}`;
  return `${value.toFixed(decimals)} ${unit}`;
}

interface ActionError {
  status: number;
  message: string;
}

function interpretActionError(e: unknown): ActionError {
  if (!(e instanceof ApiError)) {
    const message = e instanceof Error ? e.message : String(e);
    return { status: 0, message };
  }
  const body = (e.body ?? {}) as Record<string, unknown>;
  const detail = typeof body.detail === "string" ? body.detail : undefined;

  // 412 from shake.start when wait_for_temperature=true and the heater
  // didn't reach the band in time. Body shape matches the sealer's
  // temperature interlock: { actual_c, setpoint_c, tolerance_c, retry_after_s }.
  if (e.status === 412) {
    const actual = typeof body.actual_c === "number" ? body.actual_c : null;
    const setpoint =
      typeof body.setpoint_c === "number" ? body.setpoint_c : null;
    const tol = typeof body.tolerance_c === "number" ? body.tolerance_c : null;
    const retry =
      typeof body.retry_after_s === "number" ? body.retry_after_s : e.retryAfterS;
    if (actual !== null && setpoint !== null && tol !== null) {
      const retryStr =
        retry !== null && retry > 0 ? ` Try again in ~${Math.ceil(retry)} s.` : "";
      return {
        status: 412,
        message: `Heater at ${actual.toFixed(0)} °C, need ${setpoint.toFixed(
          0,
        )} ±${tol} °C.${retryStr}`,
      };
    }
    return { status: 412, message: detail ?? "Device precondition not met." };
  }

  if (e.status === 423) {
    const claimedBy = body.claimed_by as { owner?: string } | undefined;
    const owner = claimedBy?.owner;
    return {
      status: 423,
      message: owner
        ? `Device claim is held by ${owner}. Try again later.`
        : detail ?? "Device is busy with another caller.",
    };
  }

  if (e.status === 409) {
    const msg = detail ?? "Action rejected.";
    const hint = /init|startup|connect/i.test(msg) ? " Click Startup first." : "";
    return { status: 409, message: msg + hint };
  }

  return { status: e.status, message: detail ?? e.message };
}

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

export function ShakerTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const shaker = parseShaker(snapshot);
  const [, startTransition] = useTransition();
  const { locked, countdown, toggle } = useControlLock(snapshot.id);
  const [actionError, setActionError] = useState<ActionError | null>(null);

  // Next-cycle parameters for shake.start. Local-only state (the device
  // doesn't echo a stored cycle config; shake.start takes them inline).
  const [cycleTempC, setCycleTempC] = useState<number>(DEFAULT_TEMP_C);
  const [cycleSpeed, setCycleSpeed] = useState<number>(DEFAULT_SPEED);
  const [cycleDurationS, setCycleDurationS] =
    useState<number>(DEFAULT_DURATION_S);
  const [waitForTemp, setWaitForTemp] = useState<boolean>(false);

  const status = snapshot.status.equipment_status;
  const isBusy = status === "busy";
  const isReady = status === "ready";
  const isRequiresInit = status === "requires_init";
  const isDryRun = status === "dry_run";
  // Motor side may be healthy even when the device is `degraded` because of
  // a heater-side fault (e.g. SC25XR `cal3` RTD calibration). Treat that as
  // motor-controls-OK so the operator can still shake without the heater.
  const motorHealthy =
    shaker.motorState === "idle" ||
    shaker.motorState === "running" ||
    shaker.motorState === "shaking";
  const isDegradedMotorOk = status === "degraded" && motorHealthy;
  const heaterHealthy = shaker.heaterState === "stable";

  const controlsDisabled = locked;

  function exec<T>(fn: () => Promise<T>) {
    setActionError(null);
    startTransition(() => {
      fn().catch((err: unknown) => {
        setActionError(interpretActionError(err));
      });
    });
  }

  useEffect(() => {
    if (actionError && isReady) setActionError(null);
  }, [actionError, isReady]);

  // Cycle row is visible whenever the device is past startup; the Shake
  // button enables only in ready/dry_run/degraded-motor-ok. STOP lives in
  // its own always-visible row below (shake.stop is idempotent per the
  // catalog), so an abort is reachable from every state.
  const cycleRowVisible = isReady || isBusy || isDryRun || isDegradedMotorOk;
  const canShake = (isReady || isDryRun || isDegradedMotorOk) && !isBusy;
  const shakeStartValid =
    Number.isFinite(cycleTempC) &&
    cycleTempC >= TEMP_MIN &&
    cycleTempC <= TEMP_MAX &&
    Number.isInteger(cycleSpeed) &&
    cycleSpeed >= SPEED_MIN &&
    cycleSpeed <= SPEED_MAX &&
    Number.isFinite(cycleDurationS) &&
    cycleDurationS >= DURATION_MIN &&
    cycleDurationS <= DURATION_MAX;

  return (
    <TileShell
      snapshot={snapshot}
      headerRight={
        <>
          <LockButton
            locked={locked}
            countdown={countdown}
            onToggle={toggle}
            noun="shaker"
          />
          <StatusPill state={status} />
        </>
      }
    >
      {/* 2x2 metric grid: temperature pair on the top row, motor speed +
          set-temperature on the bottom row. The Actual pill is tinted by
          heater state; the Speed pill by motor state. */}
      <div className="grid grid-cols-2 gap-1.5">
        <MetricPill
          caption="Actual"
          value={fmt(shaker.actualTempC, "°C", 1)}
          tone={heaterTone(shaker.heaterState)}
          title={shaker.heaterMessage ?? undefined}
        />
        <EditablePill
          caption="Setpoint"
          current={null}
          unit="°C"
          step={1}
          min={TEMP_MIN}
          max={TEMP_MAX}
          decimals={0}
          locked={locked}
          busy={isBusy || (status === "degraded" && !heaterHealthy)}
          onSet={(v) =>
            exec(() => postShakerSetTemperature(snapshot.id, v))
          }
        />
        <MetricPill
          caption="Speed"
          value={
            shaker.speedLevel !== null
              ? `level ${shaker.speedLevel.toFixed(0)}`
              : "level —"
          }
          tone={motorTone(shaker.motorState, shaker.speedLevel)}
          title={shaker.motorMessage ?? undefined}
        />
        <MetricPill
          caption="Heater"
          value={shaker.heaterState ?? "—"}
          tone={heaterTone(shaker.heaterState)}
          title={shaker.heaterMessage ?? undefined}
        />
      </div>

      {/* Cycle parameters for shake.start. Inline form so the operator
          picks temperature + speed + duration in one place and clicks
          Shake to fire one timed cycle. The device owns the duration
          timer per recipe v2 §3.5. STOP sits next to Shake so an
          operator can abort the running cycle from the same row. */}
      {cycleRowVisible && (
        <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5 dark:border-slate-700 dark:bg-slate-800/40">
          <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
            Cycle
          </span>
          <label className="flex items-center gap-1 text-[10px] text-ink-subtle dark:text-slate-500">
            <input
              type="number"
              min={TEMP_MIN}
              max={TEMP_MAX}
              step={1}
              value={cycleTempC}
              disabled={controlsDisabled}
              onChange={(e) => setCycleTempC(parseFloat(e.target.value))}
              aria-label="Cycle temperature in degrees C"
              className="h-7 w-14 rounded border border-slate-200 bg-white px-1 text-right font-mono text-xs tabular-nums text-ink outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-300 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
            />
            °C
          </label>
          <label className="flex items-center gap-1 text-[10px] text-ink-subtle dark:text-slate-500">
            <input
              type="number"
              min={SPEED_MIN}
              max={SPEED_MAX}
              step={1}
              value={cycleSpeed}
              disabled={controlsDisabled}
              onChange={(e) =>
                setCycleSpeed(parseInt(e.target.value, 10) || 0)
              }
              aria-label="Cycle speed level 1-9"
              className="h-7 w-10 rounded border border-slate-200 bg-white px-1 text-right font-mono text-xs tabular-nums text-ink outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-300 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
            />
            level
          </label>
          <label className="flex items-center gap-1 text-[10px] text-ink-subtle dark:text-slate-500">
            <input
              type="number"
              min={DURATION_MIN}
              max={DURATION_MAX}
              step={1}
              value={cycleDurationS}
              disabled={controlsDisabled}
              onChange={(e) => setCycleDurationS(parseFloat(e.target.value))}
              aria-label="Cycle duration in seconds"
              className="h-7 w-16 rounded border border-slate-200 bg-white px-1 text-right font-mono text-xs tabular-nums text-ink outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-300 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
            />
            s
          </label>
          <label
            className="flex items-center gap-1 text-[10px] text-ink-subtle dark:text-slate-500"
            title="When checked, the device waits for the heater to reach the cycle temperature (within tolerance) before starting the duration countdown. Refuses with HTTP 412 if the configured timeout is exceeded."
          >
            <input
              type="checkbox"
              checked={waitForTemp && heaterHealthy}
              disabled={controlsDisabled || !heaterHealthy}
              onChange={(e) => setWaitForTemp(e.target.checked)}
              aria-label="Wait for heater to reach setpoint before counting down"
              title={
                !heaterHealthy
                  ? "Heater not stable; wait-for-temperature would hang."
                  : undefined
              }
              className="h-3 w-3 rounded border-slate-300 disabled:opacity-50 dark:border-slate-600"
            />
            wait
          </label>
          <TileButton
            onClick={() =>
              exec(() =>
                postShakerShakeStart(snapshot.id, {
                  speed_level: cycleSpeed,
                  temperature_c: cycleTempC,
                  duration_s: cycleDurationS,
                  wait_for_temperature: waitForTemp,
                }),
              )
            }
            disabled={controlsDisabled || !canShake || !shakeStartValid}
            variant="primary"
            title={!canShake ? "Device is busy or not ready" : undefined}
          >
            Shake
          </TileButton>
        </div>
      )}

      {/* Out-of-row actions. STOP is always visible so the cycle can be
          aborted from any state — including requires_init, where the cycle
          row (and its Shake button) is hidden. Only the control lock gates
          it. Startup appears only while uninitialised. */}
      <div className="flex flex-wrap items-center gap-1">
        {isRequiresInit && (
          <TileButton
            onClick={() => exec(() => postShakerStartup(snapshot.id))}
            disabled={controlsDisabled}
            variant="primary"
          >
            Startup
          </TileButton>
        )}
        <TileButton
          onClick={() => exec(() => postShakerShakeStop(snapshot.id))}
          disabled={controlsDisabled}
          variant="danger"
          title="Abort the current cycle (halts motor and heating)."
        >
          STOP
        </TileButton>
      </div>

      {actionError !== null && (
        <div
          role="status"
          className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
        >
          {actionError.status > 0 && (
            <span className="shrink-0 font-mono font-semibold">
              {actionError.status}
            </span>
          )}
          <span className="leading-snug">{actionError.message}</span>
        </div>
      )}
    </TileShell>
  );
}
