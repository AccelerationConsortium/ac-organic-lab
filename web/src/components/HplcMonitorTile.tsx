import type { ReactNode } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { FetchErrorBand } from "./FetchErrorBand";
import { StatusPill } from "./StatusPill";
import { TileShell } from "./TileShell";
import { AuthGatedLink } from "./AuthGatedLink";

type Tone = "neutral" | "ok" | "warn" | "muted";

// Match the HTE HPLC/plate-reader sections without importing their controls.
const TONE_CLASSES: Record<Tone, string> = {
  neutral: "border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/40",
  ok: "border-emerald-300 bg-emerald-50 dark:border-emerald-700 dark:bg-emerald-950/40",
  warn: "border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/40",
  muted: "border-slate-200 bg-slate-100 dark:border-slate-700 dark:bg-slate-800/20",
};

const DOT_CLASSES: Record<Tone, string> = {
  neutral: "bg-slate-400 dark:bg-slate-500",
  ok: "bg-emerald-400",
  warn: "bg-amber-400",
  muted: "bg-slate-300 dark:bg-slate-600",
};

/** Caption over value, so four fit across a two-column tile. */
function Stat({ caption, value, tone = "neutral", title }: {
  caption: string; value: string; tone?: Tone; title?: string;
}) {
  return (
    <div className={`flex h-9 min-w-0 flex-col justify-center rounded-md border px-2 ${TONE_CLASSES[tone]}`} title={title ?? `${caption}: ${value}`}>
      <span className="truncate text-[10px] uppercase leading-3 tracking-wider text-ink-subtle dark:text-slate-400">{caption}</span>
      <span className="truncate text-xs font-semibold leading-4 tabular-nums text-ink dark:text-slate-100">{value}</span>
    </div>
  );
}

function Diagnostic({ caption, value, tone, title }: {
  caption: string; value: string; tone: Tone; title?: string;
}) {
  return (
    <span className="inline-flex shrink-0 items-center gap-1" title={title}>
      <span className={`h-1.5 w-1.5 rounded-full ${DOT_CLASSES[tone]}`} aria-hidden />
      {caption} <span className="font-semibold text-ink dark:text-slate-100">{value}</span>
    </span>
  );
}

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section aria-label={label} className="flex min-w-0 items-center gap-x-3 text-[11px] leading-4 text-ink-subtle dark:text-slate-400">
      {children}
    </section>
  );
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

function label(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function nonnegative(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function age(value: unknown): string {
  const seconds = nonnegative(value);
  if (seconds === null) return "Unknown";
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function displayState(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1).replace(/_/g, " ");
}

/** ChemStation observations only: no lock, lifecycle handlers, or API writes. */
export function HplcMonitorTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const { status, fetch_error: fetchError } = snapshot;
  // Never present cached positive observations as live after a failed poll.
  const details = fetchError ? {} : record(status.details);
  const components = fetchError ? {} : status.components ?? {};
  const metrics = fetchError ? {} : status.metrics ?? {};
  const native = record(details.native_readback);
  const nativeAvailable = native.available !== false &&
    components.native_status_snapshot?.connected === true;
  const nativeState = nativeAvailable
    ? label(metrics.instrument_state?.value) ?? label(native.acquisition_state)
    : null;
  // The reader's fixed diagnostic (e.g. "reader deadline exceeded during DDE
  // connect") is the most direct pointer to why readiness is unobserved.
  const readbackProblem = native.available === false ? label(native.problem) : null;
  const activity = fetchError ? "unknown" : status.activity ?? snapshot.activity ?? "unknown";
  const inferred = activity === "running" && details.file_activity_inferred === true;
  const activityLabel = activity === "running" ? (inferred ? "Running · inferred" : "Running")
    : activity === "idle" ? "Idle" : "Unknown";
  const queueLength = nonnegative(details.queue_length);
  const queueKnown = nativeAvailable && details.queue_available === true &&
    queueLength !== null && Number.isInteger(queueLength);
  const currentRun = nativeAvailable ? record(details.current_run) : {};
  const currentTitle = label(currentRun.title) ?? label(currentRun.sample);
  const lastResult = record(details.last_result);
  const resultName = label(lastResult.name)?.split(/[\\/]/).pop() || "Not observed";
  const resultsFolders = Array.isArray(details.results_folders)
    ? details.results_folders.map(label).filter((path): path is string => path !== null)
    : [];
  const folderReadable = components.data_path?.state === "available";

  function module(key: string, caption: string) {
    const component = nativeAvailable ? components[key] : undefined;
    const state = label(component?.state);
    const normalized = state?.toLowerCase();
    const tone: Tone = !state || normalized === "unknown" ? "muted"
      : normalized === "error" || normalized === "not_ready" || component?.connected === false ? "warn"
      : normalized === "ready" || normalized === "idle" ? "ok" : "neutral";
    return <Stat caption={caption} value={state ? displayState(state) : "Not observed"} tone={tone}
      title={component?.message ? `${caption}: ${component.message}` : undefined} />;
  }

  function diagnostic(key: string, caption: string, values: Record<string, string> = {}, supporting = false) {
    const component = components[key];
    const state = label(component?.state);
    const value = state ? values[state.toLowerCase()] ?? displayState(state) : "Unknown";
    // An optional supporting service being stopped is not an instrument fault.
    const tone: Tone = component?.connected === true ? "ok"
      : !supporting && state === "error" ? "warn" : "muted";
    return <Diagnostic caption={caption} value={value} tone={tone}
      title={supporting ? "Supporting service only; not an instrument-readiness indicator" : undefined} />;
  }

  return (
    <TileShell snapshot={snapshot}
      subtitleExtra="Read-only monitoring"
      headerRight={
        <>
          {snapshot.id === "lle_hplc" && (
            <AuthGatedLink href="/equipment/lle_hplc/control"
              className="inline-flex h-6 items-center rounded-md border border-slate-200 px-2 text-xs font-medium text-sky-700 hover:bg-sky-50 dark:border-slate-700 dark:text-sky-300 dark:hover:bg-slate-800"
              title="Open the simulated acquisition interface under SDL2 sign-in">
              Control preview
            </AuthGatedLink>
          )}
          <StatusPill state={fetchError ? "unknown" : status.equipment_status} />
        </>
      }
      footerMessageTitle={[status.message, readbackProblem && `Readback: ${readbackProblem}`].filter(Boolean).join(" · ") || undefined}
      footerLeft={fetchError ? "Status unavailable until the monitor reconnects." : undefined}
    >
      {fetchError && <FetchErrorBand error={fetchError} />}
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          <section aria-label="Instrument status" className="grid grid-cols-2 gap-1.5">
            <Stat caption="Native state" value={nativeState ?? "Not observed"}
              tone={nativeState ? "neutral" : "muted"}
              title={readbackProblem ? `Native state not observed: ${readbackProblem}` : undefined} />
            <Stat caption="Activity" value={activityLabel}
              tone={inferred ? "warn" : activity === "running" ? "ok" : activity === "idle" ? "neutral" : "muted"}
              title={inferred ? "Inferred from recent result-file writes, not native acquisition status" : undefined} />
          </section>
          <section aria-label="Run queue" className="grid grid-cols-2 gap-1.5">
            <Stat caption="Pending" value={queueKnown ? String(queueLength) : "Not observed"}
              tone={queueKnown ? "neutral" : "muted"} />
            <Stat caption="Current run" value={currentTitle ?? "Not observed"}
              tone={currentTitle ? "neutral" : "muted"} />
          </section>
        </div>
        {readbackProblem && (
          <p className="truncate rounded-md border border-amber-300 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200"
            title="Reported by the workstation's status reader; instrument readiness is unobserved">
            Readback unavailable: {readbackProblem}
          </p>
        )}
        <section aria-label="Instrument modules" className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
          {module("module_hip_sampler", "HiP sampler")}
          {module("module_binary_pump", "Binary pump")}
          {module("module_column_compartment", "Column comp.")}
          {module("module_dad", "DAD")}
        </section>
        <Line label="Latest result">
          <span className="shrink-0 uppercase tracking-wider">Last result</span>
          <span className="min-w-0 truncate font-mono text-ink dark:text-slate-100" title={resultName}>{resultName}</span>
          <span className="shrink-0" title="Result file modification time; not proof of run completion">
            Updated {age(lastResult.age_s ?? metrics.last_result_age?.value)}
          </span>
        </Line>
        <Line label="Software diagnostics">
          {diagnostic("chemstation_acquisition", "Acquisition", { running: "Open" })}
          {diagnostic("chemstation_analysis", "Analysis", { running: "Open" })}
          {diagnostic("data_service", "Data service", {}, true)}
          <Diagnostic caption="Folder" value={folderReadable ? "Readable" : "Unknown"}
            tone={folderReadable ? "ok" : "muted"}
            title="Open means the process is present; readable means the configured folder can be read. Neither establishes module readiness." />
          {resultsFolders.length ? resultsFolders.map((path, index) =>
            <span key={`${index}-${path}`} className="min-w-0 truncate font-mono"
              title="Configured results folder watched by the monitor; not necessarily the active method's output folder">{path}</span>
          ) : <span className="min-w-0 truncate">Path not available</span>}
        </Line>
      </div>
    </TileShell>
  );
}
