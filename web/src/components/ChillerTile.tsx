"use client";

import { useEffect, useState, type FormEvent } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import {
  postChillerSetPumpSpeed,
  postChillerSetTemperature,
  postChillerStart,
  postChillerStartup,
  postChillerStop,
} from "@/lib/api";
import type { Parse412 } from "@/lib/action-error";
import { useActionError } from "@/lib/use-action-error";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

// Pump range is fixed by the hardware (RC 2 lite technical data).
const PUMP_MIN_RPM = 1000;
const PUMP_MAX_RPM = 3200;
const DEFAULT_PUMP_RPM = 2000;

// Fallbacks only. The real setpoint bounds are installation-specific (they
// depend on whether an external heating accessory is fitted) and arrive on
// every envelope as `details.setpoint_limits_c`; these apply just until the
// first poll lands.
const FALLBACK_MIN_C = -20;
const FALLBACK_MAX_C = 30;

type TemperingState =
  | "at_setpoint"
  | "cooling"
  | "below_setpoint"
  | "unknown";

interface ChillerState {
  bathC: number | null;
  setpointC: number | null;
  pumpRpm: number | null;
  temperingState: TemperingState | null;
  temperingMessage: string | null;
  pumpMessage: string | null;
  minC: number;
  maxC: number;
  /** What the service last *commanded* the compressor to do. The NAMUR
   *  interface cannot report the real thing, so this is never treated as a
   *  reading — it only annotates the cooling pill. */
  temperingCommanded: boolean | null;
  /** False while the serial port is closed: the chiller may well still be
   *  running, we just cannot see it. */
  hardwareObserved: boolean;
}

function parseChiller(snapshot: EquipmentSnapshot): ChillerState {
  const metrics = snapshot.status.metrics ?? {};
  const components = snapshot.status.components ?? {};
  const details = (snapshot.status.details ?? {}) as Record<string, unknown>;
  const num = (key: string): number | null => {
    const v = metrics[key]?.value;
    return typeof v === "number" ? v : null;
  };
  const tempering = components["tempering"];
  const pump = components["pump"];
  const limits = details["setpoint_limits_c"];
  const [minC, maxC] =
    Array.isArray(limits) &&
    limits.length === 2 &&
    typeof limits[0] === "number" &&
    typeof limits[1] === "number"
      ? [limits[0], limits[1]]
      : [FALLBACK_MIN_C, FALLBACK_MAX_C];
  const commanded = details["tempering_commanded"];
  return {
    bathC: num("actual_temperature"),
    setpointC: num("setpoint_temperature"),
    pumpRpm: num("pump_speed"),
    temperingState:
      tempering && typeof tempering.state === "string"
        ? (tempering.state as TemperingState)
        : null,
    temperingMessage: tempering?.message ?? null,
    pumpMessage: pump?.message ?? null,
    minC,
    maxC,
    temperingCommanded: typeof commanded === "boolean" ? commanded : null,
    hardwareObserved: details["hardware_state_observed"] !== false,
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

function temperingTone(state: TemperingState | null): Tone {
  if (state === "at_setpoint") return "ok";
  if (state === "cooling" || state === "below_setpoint") return "warn";
  return "muted";
}

function pumpTone(activity: string | undefined): Tone {
  if (activity === "running") return "warn";
  if (activity === "idle") return "neutral";
  return "muted";
}

function fmt(value: number | null, unit: string, decimals: number): string {
  if (value === null || value === undefined) return `— ${unit}`;
  return `${value.toFixed(decimals)} ${unit}`;
}

/**
 * The chiller's two 412 shapes (see the device's `/agent-docs`).
 *
 * The temperature one is the important one: it means the bath did not reach
 * the setpoint in time and **the chiller is still running**. A reader who
 * takes a 412 to mean "nothing happened" would walk away from a chiller they
 * just started, so `still_running` is spelled out rather than implied.
 */
const parseChiller412: Parse412 = (body) => {
  if (body.still_running === true) {
    const actual = typeof body.actual_c === "number" ? body.actual_c : null;
    const setpoint = typeof body.setpoint_c === "number" ? body.setpoint_c : null;
    const waited = typeof body.waited_s === "number" ? body.waited_s : null;
    const where =
      actual !== null && setpoint !== null
        ? ` Bath ${actual.toFixed(1)} °C, target ${setpoint.toFixed(1)} °C.`
        : "";
    const how = waited !== null ? ` after ${Math.round(waited)} s` : "";
    return `Setpoint not reached${how} — the chiller is still running.${where}`;
  }
  const subsystem = body.blocked_subsystem;
  if (subsystem === "temperature" || subsystem === "pump") {
    const reads = Array.isArray(body.readback_errors)
      ? body.readback_errors.filter((e): e is string => typeof e === "string")
      : [];
    const detail = reads.length ? ` (${reads[0]})` : "";
    return `The ${subsystem} readback is failing, so this control is unavailable${detail}.`;
  }
  if (subsystem === "service") {
    return "A device error has not cleared yet. Only Stop is available until it does.";
  }
  return null;
};

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
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
        {caption}
      </span>
      <span className="ml-auto text-xs font-semibold text-ink dark:text-slate-100 tabular-nums">
        {value}
      </span>
    </div>
  );
}

function SetpointPill({
  actual,
  current,
  min,
  max,
  tone,
  title,
  disabled,
  onSet,
}: {
  actual: string;
  current: number | null;
  min: number;
  max: number;
  tone: Tone;
  title?: string;
  disabled: boolean;
  onSet: (value: number) => void;
}) {
  const [draft, setDraft] = useState<string>(current?.toFixed(1) ?? "");

  useEffect(() => {
    setDraft(current?.toFixed(1) ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (disabled) return;
    const parsed = parseFloat(draft);
    // Out of range is refused by the device with 422 anyway; stopping here
    // keeps the operator from firing a request that cannot succeed.
    if (Number.isNaN(parsed) || parsed < min || parsed > max) return;
    onSet(parsed);
  }

  return (
    <form
      onSubmit={handleSubmit}
      className={`flex h-7 items-center gap-1 rounded-md border px-2 ${TONE_CLASSES[tone]}`}
      title={title}
    >
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
        Bath
      </span>
      <span className="shrink-0 text-xs font-semibold text-ink dark:text-slate-100 tabular-nums">
        {actual}
      </span>
      <span className="shrink-0 text-[10px] text-ink-subtle dark:text-slate-400">
        set
      </span>
      <input
        type="number"
        inputMode="decimal"
        step={0.1}
        min={min}
        max={max}
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        aria-label="Chiller setpoint in degrees C"
        className="ml-auto w-14 min-w-0 rounded border border-slate-200 bg-white px-1 py-0 text-right text-xs tabular-nums text-ink outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-300 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
      />
      <span className="shrink-0 text-[10px] text-ink-subtle dark:text-slate-400">
        °C
      </span>
      <TileButton
        type="submit"
        size="small"
        variant="primary"
        ariaLabel="Set chiller setpoint"
        disabled={disabled}
      >
        Set
      </TileButton>
    </form>
  );
}

export function ChillerTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const chiller = parseChiller(snapshot);
  const { locked, countdown, toggle } = useControlLock(snapshot.id);
  const { actionError, setActionError, exec } = useActionError(parseChiller412);
  const [pumpRpm, setPumpRpm] = useState<number>(DEFAULT_PUMP_RPM);

  const status = snapshot.status.equipment_status;
  const activity = snapshot.activity ?? snapshot.status.activity;
  const allowed = snapshot.status.allowed_actions ?? [];
  const isRequiresInit = status === "requires_init";
  const isRunning = activity === "running";

  useEffect(() => {
    if (actionError && status === "ready") setActionError(null);
  }, [actionError, status, setActionError]);

  // The device is the authority on what it would honour right now; its
  // `allowed_actions` and its 412 refusals come from one function, so
  // mirroring the list here can never disagree with what a click would get.
  const can = (action: string) => allowed.includes(action);
  const pumpValid =
    Number.isInteger(pumpRpm) && pumpRpm >= PUMP_MIN_RPM && pumpRpm <= PUMP_MAX_RPM;

  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      // The lifecycle row appears ONLY to reconnect a closed serial port.
      // There is deliberately no power toggle and no STOP here: on this
      // device `/control/shutdown` does not stop the chiller, and a button in
      // the power position that leaves the hardware running is a lie the
      // operator would only discover from a warming reaction. Starting and
      // stopping the cooling live in the run row below, named for what they
      // actually do.
      lifecycle={
        isRequiresInit
          ? {
              isOn: false,
              initLabel: "CONNECT",
              onPowerToggle: () => exec(() => postChillerStartup(snapshot.id)),
              disabled: locked,
              powerTitle:
                "Reopen the serial port. The chiller itself is unaffected and may already be running.",
            }
          : undefined
      }
      headerRight={
        <LockButton
          locked={locked}
          countdown={countdown}
          onToggle={toggle}
          noun="chiller"
        />
      }
      footerLeft={
        !chiller.hardwareObserved
          ? "Serial port closed — the chiller is unaffected and may still be running."
          : undefined
      }
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <SetpointPill
          actual={fmt(chiller.bathC, "°C", 1)}
          current={chiller.setpointC}
          min={chiller.minC}
          max={chiller.maxC}
          tone={temperingTone(chiller.temperingState)}
          title={`Permitted range ${chiller.minC} to ${chiller.maxC} °C. A setpoint outside it is refused, not clamped.`}
          disabled={locked || !can("chiller.set_temperature")}
          onSet={(v) => exec(() => postChillerSetTemperature(snapshot.id, v))}
        />
        <MetricPill
          caption="Pump"
          value={fmt(chiller.pumpRpm, "rpm", 0)}
          tone={pumpTone(activity)}
          title={
            chiller.pumpMessage ??
            "Circulation is this chiller's primary operation; the tile reads it from the pump, not from what was commanded."
          }
        />
        <MetricPill
          caption="Cooling"
          value={chiller.temperingState ?? "—"}
          tone={temperingTone(chiller.temperingState)}
          title={chiller.temperingMessage ?? undefined}
        />
      </div>

      {!isRequiresInit && (
        <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5 dark:border-slate-700 dark:bg-slate-800/40">
          <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
            Run
          </span>
          <label className="flex items-center gap-1 text-[10px] text-ink-subtle dark:text-slate-400">
            <input
              type="number"
              min={PUMP_MIN_RPM}
              max={PUMP_MAX_RPM}
              step={100}
              value={pumpRpm}
              disabled={locked}
              onChange={(e) => setPumpRpm(parseInt(e.target.value, 10) || 0)}
              aria-label="Pump speed in rpm"
              className="h-7 w-16 rounded border border-slate-200 bg-white px-1 text-right text-xs tabular-nums text-ink outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-300 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
            />
            rpm
          </label>
          <TileButton
            size="small"
            ariaLabel="Set pump speed"
            disabled={locked || !pumpValid || !can("chiller.set_pump_speed")}
            onClick={() =>
              exec(() => postChillerSetPumpSpeed(snapshot.id, pumpRpm))
            }
            title="Change the pump speed without starting or stopping."
          >
            Set
          </TileButton>
          <TileButton
            variant="primary"
            disabled={locked || !pumpValid || !can("chiller.start")}
            onClick={() =>
              exec(() =>
                postChillerStart(snapshot.id, { pump_speed_rpm: pumpRpm }),
              )
            }
            title={
              isRunning
                ? "Already circulating — change the setpoint instead."
                : "Start the pump, then tempering, at the current setpoint."
            }
          >
            Start cooling
          </TileButton>
          <TileButton
            variant="danger"
            // Never gated on a readback: stopping circulation is the safety
            // action, so it stays clickable in `error` and `unknown` too.
            disabled={locked || !can("chiller.stop")}
            onClick={() => exec(() => postChillerStop(snapshot.id))}
            title="Stop tempering and the pump. The reaction will start warming."
          >
            Stop cooling
          </TileButton>
        </div>
      )}
    </TileShell>
  );
}
