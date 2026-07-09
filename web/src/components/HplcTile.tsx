"use client";

import type { ReactNode } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { MessageBand } from "./MessageBand";
import { StatusPill } from "./StatusPill";
import { TileShell } from "./TileShell";

/**
 * Kind-specific tile for `hplc` (the Agilent UPLC-MS sidecar).
 *
 * The instrument is two subsystems sharing one OpenLab acquisition pipeline, so
 * the tile separates them into labeled sections:
 *
 *   - amber callout when the device needs operator action (e.g. an OpenLab
 *     sequence paused — answers "why is it busy?" right on the tile)
 *   - HPLC (LC): pump/autosampler comms, solvent A/B + waste fluidics,
 *     column-thermostat / multisampler states
 *   - MS: vacuum, source temp, drying gas, nebulizer + turbopump/HV readiness
 *   - Run queue (instrument-wide): pending depth, current + last run, OpenLab
 *
 * Read-only by design: the device's control surface (run.submit with a full
 * gradient + per-sample well map, abort, queue.cancel, standby) is too rich to
 * form-ify in a tile. The lock chip stays in the header (hplc is a destructive
 * kind in tile-policy.ts) as the visible promise that run controls, when they
 * land, will be gated.
 */

type Tone = "neutral" | "ok" | "warn" | "muted";

const TONE_CLASSES: Record<Tone, string> = {
  neutral:
    "border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/40",
  ok: "border-emerald-300 bg-emerald-50 dark:border-emerald-700 dark:bg-emerald-950/40",
  warn: "border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/40",
  muted:
    "border-slate-200 bg-slate-100 dark:border-slate-700 dark:bg-slate-800/20",
};

interface HplcState {
  num: (key: string) => number | null;
  bool: (key: string) => boolean | null;
  compState: (key: string) => string | null;
  detail: (key: string) => unknown;
}

function parseHplc(snapshot: EquipmentSnapshot): HplcState {
  const metrics = snapshot.status.metrics ?? {};
  const components = snapshot.status.components ?? {};
  const details = (snapshot.status.details ?? {}) as Record<string, unknown>;
  return {
    num: (key) => {
      const v = metrics[key]?.value;
      return typeof v === "number" ? v : null;
    },
    bool: (key) => {
      const v = metrics[key]?.value;
      return typeof v === "boolean" ? v : null;
    },
    compState: (key) => {
      const c = components[key];
      return c && typeof c.state === "string" ? c.state : null;
    },
    detail: (key) => details[key],
  };
}

function fmt(value: number | null, unit: string, decimals: number): string {
  if (value === null) return `— ${unit}`;
  return `${value.toFixed(decimals)} ${unit}`;
}

/** Strip an Agilent result path down to the bare run name. */
function runName(raw: unknown): string | null {
  if (typeof raw !== "string" || !raw) return null;
  const base = raw.split(/[\\/]/).pop() ?? raw;
  return base.replace(/\.(sirslt|d|m)$/i, "");
}

/** A labeled subsystem block with a divider header + optional right-side pill. */
function Section({
  title,
  right,
  children,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between border-b border-slate-100 pb-0.5 dark:border-slate-800">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-ink-subtle dark:text-slate-500">
          {title}
        </span>
        {right}
      </div>
      {children}
    </div>
  );
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
      className={`flex h-8 items-center gap-1 rounded-md border px-2 ${TONE_CLASSES[tone]}`}
      title={title}
    >
      <span className="shrink-0 text-[11px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
        {caption}
      </span>
      <span className="ml-auto truncate text-xs font-semibold text-ink dark:text-slate-100 tabular-nums">
        {value}
      </span>
    </div>
  );
}

/** Small dot + label for a boolean comms/readiness signal. */
function StatusDot({ label, ok }: { label: string; ok: boolean | null }) {
  const tone =
    ok === null
      ? "bg-slate-300 dark:bg-slate-600"
      : ok
        ? "bg-emerald-500"
        : "bg-rose-500";
  return (
    <div
      className="flex items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 dark:border-slate-700 dark:bg-slate-800/40"
      title={ok === null ? `${label}: unknown` : `${label}: ${ok ? "OK" : "fault"}`}
    >
      <span className={`h-2 w-2 shrink-0 rounded-full ${tone}`} />
      <span className="text-[11px] text-ink-subtle dark:text-slate-400">{label}</span>
    </div>
  );
}

/**
 * Horizontal fill bar for a reservoir. `invert` flips the color logic for
 * waste (full = bad). `warn` (from the device's own *_low / near-capacity
 * flags) forces amber regardless.
 */
function FluidBar({
  label,
  value,
  capacity,
  warn = false,
  invert = false,
}: {
  label: string;
  value: number | null;
  capacity: number | null;
  warn?: boolean;
  invert?: boolean;
}) {
  const pct =
    value !== null && capacity && capacity > 0
      ? Math.max(0, Math.min(100, (value / capacity) * 100))
      : null;
  const fillColor = warn
    ? "bg-amber-500"
    : invert
      ? "bg-sky-500"
      : "bg-emerald-500";
  return (
    <div className="flex items-center gap-2">
      <span className="w-20 shrink-0 text-[11px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
        {label}
      </span>
      <div className="relative h-3 flex-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
        {pct !== null && (
          <div
            className={`h-full rounded-full ${fillColor} transition-all`}
            style={{ width: `${pct}%` }}
          />
        )}
      </div>
      <span className="w-28 shrink-0 text-right text-xs tabular-nums text-ink-muted dark:text-slate-400">
        {value !== null && capacity
          ? `${value.toFixed(0)}/${capacity.toFixed(0)} mL`
          : "— mL"}
      </span>
    </div>
  );
}

const COMP_TONE: Record<string, Tone> = {
  running: "ok",
  ready: "ok",
  idle: "neutral",
  paused: "warn",
  error: "warn",
  disconnected: "muted",
};

/** Compact subsystem state chip. `label` is optional — omit it in a Section
 *  header where the section title already names the subsystem. */
function CompPill({ label, state }: { label?: string; state: string | null }) {
  const tone: Tone = state ? (COMP_TONE[state] ?? "neutral") : "muted";
  return (
    <div
      className={`flex h-6 items-center gap-1 rounded-md border px-2 ${TONE_CLASSES[tone]}`}
    >
      {label && (
        <span className="text-[11px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
          {label}
        </span>
      )}
      <span className="text-xs font-semibold text-ink dark:text-slate-100">
        {state ?? "—"}
      </span>
    </div>
  );
}

export function HplcTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const h = parseHplc(snapshot);
  const { locked, countdown, toggle } = useControlLock(snapshot.id);

  const status = snapshot.status.equipment_status;
  const requiredActions = snapshot.status.required_actions ?? [];
  const needsAction = requiredActions.length > 0;

  // MS vitals
  const vacuum = h.num("vacuum_level_mbar");
  const turbopumpReady = h.bool("turbopump_ready");
  const sourceT = h.num("source_temperature_c");
  const sourceSet = h.num("source_temperature_setpoint_c");
  const sourceTone: Tone =
    sourceT !== null && sourceSet !== null
      ? Math.abs(sourceT - sourceSet) <= 5
        ? "ok"
        : "warn"
      : "neutral";
  const dryT = h.num("drying_gas_temperature_c");
  const dryFlow = h.num("drying_gas_flow_lpm");
  const nebulizer = h.num("nebulizer_pressure_psig");

  // Queue / run (instrument-wide)
  const qlen = h.detail("queue_length");
  const pending = typeof qlen === "number" ? qlen : null;
  const currentRun =
    typeof h.detail("olss_current_run") === "string"
      ? (h.detail("olss_current_run") as string)
      : null;
  const lastRun = runName(h.detail("last_run_dir"));

  // Footer: OLSS software status (non-duplicative of the callout message).
  const olss = h.detail("olss_software_status");
  const footerLeft =
    typeof olss === "string" && olss ? (
      <div className="truncate">
        OLSS: <span className="font-medium text-ink-muted dark:text-slate-400">{olss}</span>
      </div>
    ) : undefined;

  return (
    <TileShell
      snapshot={snapshot}
      footerLeft={footerLeft}
      headerRight={
        <>
          <LockButton
            locked={locked}
            countdown={countdown}
            onToggle={toggle}
            noun="instrument"
          />
          <StatusPill state={status} />
        </>
      }
    >
      {needsAction && snapshot.status.message && (
        <MessageBand tone="amber">{snapshot.status.message}</MessageBand>
      )}

      {/* ── HPLC (liquid chromatography) ── */}
      <Section title="HPLC · LC" right={<CompPill state={h.compState("hplc")} />}>
        <div className="flex flex-wrap gap-1.5">
          <StatusDot label="Pump" ok={h.bool("pump_communication_ok")} />
          <StatusDot label="Sampler" ok={h.bool("autosampler_communication_ok")} />
        </div>
        <div className="space-y-1">
          <FluidBar
            label="Solvent A"
            value={h.num("solvent_a1_volume_ml")}
            capacity={h.num("solvent_a1_capacity_ml")}
            warn={h.bool("solvent_a1_low") === true}
          />
          <FluidBar
            label="Solvent B"
            value={h.num("solvent_b1_volume_ml")}
            capacity={h.num("solvent_b1_capacity_ml")}
            warn={h.bool("solvent_b1_low") === true}
          />
          <FluidBar
            label="Waste"
            value={h.num("waste_volume_ml")}
            capacity={h.num("waste_capacity_ml")}
            warn={h.bool("waste_near_capacity") === true}
            invert
          />
        </div>
        <div className="flex flex-wrap gap-1.5">
          <CompPill label="Column" state={h.compState("column_thermostat")} />
          <CompPill label="Sampler" state={h.compState("multisampler")} />
        </div>
      </Section>

      {/* ── MS (mass spectrometer) ── */}
      <Section title="MS" right={<CompPill state={h.compState("ms")} />}>
        <div className="grid grid-cols-2 gap-1.5">
          <MetricPill
            caption="Vacuum"
            value={vacuum !== null ? `${vacuum.toExponential(1)} mbar` : "— mbar"}
            tone={turbopumpReady === false ? "warn" : turbopumpReady ? "ok" : "neutral"}
            title="Analyzer vacuum (turbopump)"
          />
          <MetricPill
            caption="Source"
            value={fmt(sourceT, "°C", 0)}
            tone={sourceTone}
            title={
              sourceSet !== null ? `Setpoint ${sourceSet.toFixed(0)} °C` : undefined
            }
          />
          <MetricPill
            caption="Dry gas"
            value={
              dryT !== null || dryFlow !== null
                ? `${dryT !== null ? dryT.toFixed(0) : "—"}°C · ${
                    dryFlow !== null ? dryFlow.toFixed(0) : "—"
                  } L/min`
                : "—"
            }
          />
          <MetricPill caption="Nebulizer" value={fmt(nebulizer, "psig", 0)} />
        </div>
        <div className="flex flex-wrap gap-1.5">
          <StatusDot label="MS link" ok={h.bool("ms_communication_ok")} />
          <StatusDot label="Turbo" ok={turbopumpReady} />
          <StatusDot label="HV" ok={h.bool("hv_ready")} />
        </div>
      </Section>

      {/* ── Run queue (instrument-wide) ── */}
      <Section
        title="Run queue"
        right={<CompPill label="OpenLab" state={h.compState("openlab_acquisition")} />}
      >
        <div className="flex items-center justify-between text-xs">
          <span className="text-ink-subtle dark:text-slate-500">Pending</span>
          <span className="tabular-nums text-ink dark:text-slate-100">
            {pending !== null ? pending : "—"}
          </span>
        </div>
        <div className="flex items-baseline gap-1.5 text-xs">
          <span className="shrink-0 text-ink-subtle dark:text-slate-500">Current:</span>
          <span
            className="truncate font-mono text-ink dark:text-slate-100"
            title={currentRun ?? undefined}
          >
            {currentRun ?? "idle"}
          </span>
        </div>
        {lastRun && (
          <div className="flex items-baseline gap-1.5 text-xs">
            <span className="shrink-0 text-ink-subtle dark:text-slate-500">Last:</span>
            <span
              className="truncate font-mono text-ink-muted dark:text-slate-400"
              title={lastRun}
            >
              {lastRun}
            </span>
          </div>
        )}
      </Section>
    </TileShell>
  );
}
