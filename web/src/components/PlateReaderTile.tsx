"use client";

import { useEffect, useState, type FormEvent } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import {
  postPlateReaderSetTemperature,
  postPlateReaderShutdown,
  postPlateReaderStartup,
  postPlateReaderStopRun,
  postPlateReaderStopTemperature,
} from "@/lib/api";
import { claimHolder, claimLabel, claimTitle } from "@/lib/claim";
import { useActionError } from "@/lib/use-action-error";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

type Tone = "neutral" | "ok" | "warn" | "muted";

const TONE_CLASSES: Record<Tone, string> = {
  neutral:
    "border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/40",
  ok: "border-emerald-300 bg-emerald-50 dark:border-emerald-700 dark:bg-emerald-950/40",
  warn: "border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/40",
  muted:
    "border-slate-200 bg-slate-100 dark:border-slate-700 dark:bg-slate-800/20",
};

// Device floor and ceiling (live OpenAPI: heat-only, no cooling module).
// Live `details.temperature_range_c` overrides these when published.
const TEMP_MIN_FALLBACK = 18;
const TEMP_MAX_FALLBACK = 65;
const DEFAULT_TEMP_C = 37;

type IncubatorState =
  | "off"
  | "at_setpoint"
  | "heating"
  | "cooling"
  | "unknown"
  | "disconnected";

function componentTone(state: string | undefined | null): Tone {
  if (!state) return "muted";
  if (state === "idle" || state === "in" || state === "stable" || state === "ready")
    return "ok";
  if (state === "error" || state === "fault") return "warn";
  if (state === "unknown" || state === "disconnected") return "muted";
  return "neutral"; // busy/reading/etc. — informative, not alarming
}

function incubatorTone(state: IncubatorState | null): Tone {
  if (state === "at_setpoint") return "ok";
  if (state === "heating" || state === "cooling") return "warn";
  if (state === "off" || state === "unknown" || state === "disconnected")
    return "muted";
  return "neutral";
}

function fmt(value: number | null, unit: string, decimals: number): string {
  if (value === null || value === undefined) return `— ${unit}`;
  return `${value.toFixed(decimals)} ${unit}`;
}

// The four Cytation sub-systems, in display order (two rows of two).
const COMPONENT_ROWS: { key: string; caption: string }[] = [
  { key: "optics", caption: "Optics" },
  { key: "incubator", caption: "Incubator" },
  { key: "plate_stage", caption: "Stage" },
  { key: "imaging", caption: "Imaging" },
];

function ComponentPill({
  caption,
  state,
  title,
}: {
  caption: string;
  state: string | null;
  title?: string;
}) {
  return (
    <div
      className={`flex h-7 items-center gap-1 rounded-md border px-2 ${TONE_CLASSES[componentTone(state)]}`}
      title={title}
    >
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
        {caption}
      </span>
      <span className="ml-auto truncate text-xs font-semibold text-ink dark:text-slate-100">
        {state ?? "—"}
      </span>
    </div>
  );
}

/** Single-line editable pill: caption + live reading + input + Set. */
function EditablePill({
  caption,
  actual,
  tone = "neutral",
  title,
  current,
  unit,
  step,
  min,
  max,
  decimals,
  locked,
  disabled: extraDisabled,
  onSet,
}: {
  caption: string;
  actual?: string;
  tone?: Tone;
  title?: string;
  current: number | null;
  unit: string;
  step: number;
  min: number;
  max: number;
  decimals: number;
  locked: boolean;
  disabled?: boolean;
  onSet: (value: number) => void;
}) {
  const [draft, setDraft] = useState<string>(
    current?.toFixed(decimals) ?? String(DEFAULT_TEMP_C),
  );

  useEffect(() => {
    setDraft(current?.toFixed(decimals) ?? String(DEFAULT_TEMP_C));
  }, [current, decimals]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (locked || extraDisabled) return;
    const parsed = parseFloat(draft);
    if (Number.isNaN(parsed)) return;
    if (parsed < min || parsed > max) return;
    onSet(parsed);
  }

  const disabled = locked || Boolean(extraDisabled);
  return (
    <form
      onSubmit={handleSubmit}
      className={`flex h-7 items-center gap-1 rounded-md border px-2 ${TONE_CLASSES[tone]}`}
      title={title}
    >
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
        {caption}
      </span>
      {actual !== undefined && (
        <span className="shrink-0 text-xs font-semibold text-ink dark:text-slate-100 tabular-nums">
          {actual}
        </span>
      )}
      <input
        type="number"
        inputMode="decimal"
        step={step}
        min={min}
        max={max}
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        aria-label="Incubator setpoint in degrees C"
        className="ml-auto w-12 min-w-0 rounded border border-slate-200 bg-white px-1 py-0 text-right text-xs tabular-nums text-ink outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-300 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
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

interface ReaderState {
  actualTempC: number | null;
  setpointTempC: number | null;
  incubatorState: IncubatorState | null;
  incubatorMessage: string | null;
  tempMinC: number;
  tempMaxC: number;
  readCount: number | null;
}

function parseReader(snapshot: EquipmentSnapshot): ReaderState {
  const metrics = snapshot.status.metrics ?? {};
  const components = snapshot.status.components ?? {};
  const details = (snapshot.status.details ?? {}) as Record<string, unknown>;
  const num = (key: string): number | null => {
    const v = metrics[key]?.value;
    return typeof v === "number" ? v : null;
  };
  const incubator = components["incubator"];
  const rawState =
    incubator && typeof incubator.state === "string" ? incubator.state : null;
  const range = details["temperature_range_c"];
  let tempMinC = TEMP_MIN_FALLBACK;
  let tempMaxC = TEMP_MAX_FALLBACK;
  if (range && typeof range === "object") {
    const lo = (range as { min?: unknown }).min;
    const hi = (range as { max?: unknown }).max;
    if (typeof lo === "number") tempMinC = lo;
    if (typeof hi === "number") tempMaxC = hi;
  }
  return {
    actualTempC: num("actual_temperature"),
    setpointTempC: num("setpoint_temperature"),
    incubatorState: rawState as IncubatorState | null,
    incubatorMessage: incubator?.message ?? null,
    tempMinC,
    tempMaxC,
    readCount: num("read_count"),
  };
}

/** `details.run`: the current or most recent timed run's progress, as the
 * device publishes it (agilent-cytation-server `RunRecord.summary`). */
interface RunSummary {
  runId: string;
  label: string | null;
  state: string;
  message: string | null;
  temperatureC: number | null;
  total: number;
  done: number;
  failed: number;
  nextAt: string | null;
}

const ACTIVE_RUN_STATES = new Set(["equilibrating", "holding", "stopping"]);

function parseRun(snapshot: EquipmentSnapshot): RunSummary | null {
  const details = (snapshot.status.details ?? {}) as Record<string, unknown>;
  const raw = details["run"];
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.run_id !== "string" || typeof r.state !== "string") return null;
  const num = (v: unknown) => (typeof v === "number" ? v : null);
  const str = (v: unknown) => (typeof v === "string" ? v : null);
  return {
    runId: r.run_id,
    label: str(r.label),
    state: r.state,
    message: str(r.message),
    temperatureC: num(r.temperature_c),
    total: num(r.timepoints_total) ?? 0,
    done: num(r.timepoints_done) ?? 0,
    failed: num(r.timepoints_failed) ?? 0,
    nextAt: str(r.next_timepoint_at),
  };
}

function runTone(run: RunSummary): Tone {
  if (ACTIVE_RUN_STATES.has(run.state)) return "neutral";
  if (run.state === "failed" || run.state === "interrupted") return "warn";
  return "muted";
}

function clockTime(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function allowedActions(snapshot: EquipmentSnapshot): string[] {
  const raw = snapshot.status.allowed_actions;
  return Array.isArray(raw)
    ? raw.filter((a): a is string => typeof a === "string")
    : [];
}

/**
 * BioTek Cytation 5 (kind: plate_reader). Compact half-height tile: template
 * lifecycle banner (ON toggle — startup/shutdown; the device has no halt
 * endpoint, so no STOP), the four sub-system components as pills, an
 * incubator setpoint row, the timed-run row (progress + Stop run), and the
 * read counter. Read/imaging controls land with the protocol-execution work.
 */
export function PlateReaderTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const reader = parseReader(snapshot);
  const { status } = snapshot;
  const components = status.components ?? {};
  const { locked, noAccess, countdown, toggle } = useControlLock(snapshot.id);
  const { actionError, exec } = useActionError();

  const st = status.equipment_status;
  const deviceOn = st !== "requires_init" && st !== "unknown";
  const advertised = allowedActions(snapshot);
  // A live claim is one the dashboard cannot acquire (its passthrough takes
  // a fresh per-request claim), so every control here would 423. Disable
  // them and say who holds it instead of letting the operator find out from
  // an error toast. See lib/claim.ts.
  const claim = claimHolder(status);
  const claimed = claim !== null;
  const canSetTemp =
    !locked && !claimed && advertised.includes("incubator.set_temperature");
  const incubatorOn =
    reader.setpointTempC !== null ||
    (reader.incubatorState != null &&
      reader.incubatorState !== "off" &&
      reader.incubatorState !== "disconnected");
  const canStopTemp =
    !locked && !claimed && advertised.includes("incubator.stop") && incubatorOn;
  // A timed run holds the instrument for hours and rejects every other verb,
  // so its stop lives on the tile: stopping must never depend on the
  // assistant (which can also propose run.stop).
  const run = parseRun(snapshot);
  const runActive = run !== null && ACTIVE_RUN_STATES.has(run.state);
  const canStopRun =
    !locked && !claimed && runActive && run?.state !== "stopping" &&
    advertised.includes("run.stop");

  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      lifecycle={{
        isOn: deviceOn,
        initLabel: "INIT",
        onPowerToggle: () =>
          deviceOn
            ? exec(() => postPlateReaderShutdown(snapshot.id), {
                action: "shutdown",
              })
            : exec(() => postPlateReaderStartup(snapshot.id), {
                action: "startup",
              }),
        disabled: locked || claimed,
        powerTitle: claim
          ? claimTitle(claim)
          : locked
            ? noAccess
              ? "No access"
              : "Sign in to control"
            : deviceOn
              ? "Device is on — click to shut down"
              : "Device is off — click to start up",
      }}
      headerRight={
        <>
          <LockButton
            locked={locked}
            countdown={countdown}
            onToggle={toggle}
            noun="plate reader"
          />
          <StatusPill state={st} />
        </>
      }
    >
      {/* Who holds the device, when anyone does. Rendered above the
          sub-systems because it explains why the controls below are inert —
          the reader is healthy and simply belongs to someone else, which is
          not something `equipment_status` can express (it stays `ready`,
          per STATUS_SPEC §2.3: a claim is not an operation in progress). */}
      {claim && (
        <div
          className="flex items-center gap-1.5 rounded-md border border-sky-300 bg-sky-50 px-2 py-1 dark:border-sky-700 dark:bg-sky-950/40"
          title={claimTitle(claim)}
        >
          <span
            aria-hidden="true"
            className="h-1.5 w-1.5 shrink-0 rounded-full bg-sky-500"
          />
          <span className="truncate text-[11px] font-medium text-sky-900 dark:text-sky-200">
            {claimLabel(claim)}
          </span>
        </div>
      )}

      {/* Four sub-systems as pills, two per row. */}
      <div className="grid grid-cols-2 gap-1.5">
        {COMPONENT_ROWS.map(({ key, caption }) => {
          const c = components[key];
          return (
            <ComponentPill
              key={key}
              caption={caption}
              state={c?.state ?? null}
              title={c?.message ?? undefined}
            />
          );
        })}
      </div>

      {/* Incubator setpoint: live actual + editable target + Off.
          Holding a temperature is not an operation in progress — the
          device stays ready — so this row is independent of busy. */}
      <div className="flex flex-wrap items-center gap-1.5">
        <EditablePill
          caption="Temp"
          actual={fmt(reader.actualTempC, "°C", 1)}
          tone={incubatorTone(reader.incubatorState)}
          title={reader.incubatorMessage ?? undefined}
          current={reader.setpointTempC}
          unit="°C"
          step={1}
          min={reader.tempMinC}
          max={reader.tempMaxC}
          decimals={0}
          locked={locked}
          disabled={!canSetTemp}
          onSet={(v) =>
            exec(() => postPlateReaderSetTemperature(snapshot.id, v), {
              action: "incubator.set_temperature",
            })
          }
        />
        <TileButton
          onClick={() =>
            exec(() => postPlateReaderStopTemperature(snapshot.id), {
              action: "incubator.stop",
            })
          }
          disabled={!canStopTemp}
          variant="danger"
          size="small"
          title={
            incubatorOn
              ? "End temperature control; the incubator drifts to ambient"
              : "Incubator is already off"
          }
        >
          Off
        </TileButton>
      </div>

      {/* Timed run (hold a setpoint, measure on a schedule): progress while
          one is active, how the last one ended otherwise. */}
      {run && (
        <div
          className={`flex h-7 items-center gap-1.5 rounded-md border px-2 ${TONE_CLASSES[runTone(run)]}`}
          title={[run.label, run.runId, run.message].filter(Boolean).join(" · ")}
        >
          <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
            {runActive ? "Run" : "Last run"}
          </span>
          <span className="truncate text-xs font-semibold text-ink dark:text-slate-100 tabular-nums">
            {run.state} · {run.done}/{run.total}
            {run.failed > 0 ? ` (${run.failed} failed)` : ""}
            {runActive && run.temperatureC !== null ? ` · ${run.temperatureC} °C` : ""}
            {run.state === "holding" && clockTime(run.nextAt)
              ? ` · next ${clockTime(run.nextAt)}`
              : ""}
          </span>
          {runActive && (
            <span className="ml-auto">
              <TileButton
                onClick={() =>
                  exec(() => postPlateReaderStopRun(snapshot.id), {
                    action: "run.stop",
                  })
                }
                disabled={!canStopRun}
                variant="danger"
                size="small"
                title={
                  claim
                    ? claimTitle(claim)
                    : run.state === "stopping"
                      ? "Stopping after the measurement in flight"
                      : "Stop the timed run after the measurement in flight"
                }
              >
                Stop run
              </TileButton>
            </span>
          )}
        </div>
      )}

      {/* Read counter — one line. */}
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
          Read Count
        </span>
        <span className="text-xs font-semibold text-ink dark:text-slate-100 tabular-nums">
          {typeof reader.readCount === "number"
            ? reader.readCount.toLocaleString()
            : "—"}
        </span>
      </div>
    </TileShell>
  );
}
