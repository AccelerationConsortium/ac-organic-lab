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

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="min-w-0 space-y-1.5">
      <h4 className="border-b border-slate-100 pb-0.5 text-[11px] font-semibold uppercase tracking-wider text-ink-subtle dark:border-slate-800 dark:text-slate-400">
        {title}
      </h4>
      {children}
    </section>
  );
}

function Pill({ caption, value, tone = "neutral", title }: {
  caption: string; value: string; tone?: Tone; title?: string;
}) {
  return (
    <div className={`flex h-8 min-w-0 items-center gap-1 rounded-md border px-2 ${TONE_CLASSES[tone]}`} title={title ?? `${caption}: ${value}`}>
      <span className="shrink-0 text-[11px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">{caption}</span>
      <span className="ml-auto truncate text-xs font-semibold tabular-nums text-ink dark:text-slate-100">{value}</span>
    </div>
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

  function module(key: string, caption: string) {
    const component = nativeAvailable ? components[key] : undefined;
    const state = label(component?.state);
    const normalized = state?.toLowerCase();
    const tone: Tone = !state || normalized === "unknown" ? "muted"
      : normalized === "error" || normalized === "not_ready" || component?.connected === false ? "warn"
      : normalized === "ready" || normalized === "idle" ? "ok" : "neutral";
    return <Pill caption={caption} value={state ? displayState(state) : "Not observed"} tone={tone} />;
  }

  function diagnostic(key: string, caption: string, values: Record<string, string> = {}, supporting = false) {
    const component = components[key];
    const state = label(component?.state);
    const value = state ? values[state.toLowerCase()] ?? displayState(state) : "Unknown";
    // An optional supporting service being stopped is not an instrument fault.
    const tone: Tone = component?.connected === true ? "ok"
      : !supporting && state === "error" ? "warn" : "muted";
    return <Pill caption={caption} value={value} tone={tone}
      title={supporting ? "Supporting service only; not an instrument-readiness indicator" : undefined} />;
  }

  return (
    <TileShell snapshot={snapshot}
      headerRight={<StatusPill state={fetchError ? "unknown" : status.equipment_status} />}
      bannerExtra={
        <>
        <span className="inline-flex h-6 items-center rounded-md border border-slate-200 bg-slate-100 px-2 text-[10px] font-semibold uppercase tracking-wider text-ink-subtle dark:border-slate-700 dark:bg-slate-800/20 dark:text-slate-400">
          Read-only monitoring
        </span>
        {snapshot.id === "lle_hplc" && (
          <AuthGatedLink href="/equipment/lle_hplc/control"
            className="inline-flex min-h-8 items-center rounded-md border border-slate-200 px-2 text-xs font-medium text-sky-700 hover:bg-sky-50 dark:border-slate-700 dark:text-sky-300 dark:hover:bg-slate-800"
            title="Open the simulated acquisition interface under SDL2 sign-in">
            Control preview
          </AuthGatedLink>
        )}
        </>
      }
      footerLeft={fetchError ? "Status unavailable until the monitor reconnects." : undefined}
    >
      {fetchError && <FetchErrorBand error={fetchError} />}
      <div className="flex min-w-0 flex-col gap-2">
        <Section title="Instrument modules">
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {module("module_hip_sampler", "HiP sampler")}
            {module("module_binary_pump", "Binary pump")}
            {module("module_column_compartment", "Column comp.")}
            {module("module_dad", "DAD")}
          </div>
        </Section>
        <Section title="Instrument status">
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            <Pill caption="Native state" value={nativeState ?? "Not observed"}
              tone={nativeState ? "neutral" : "muted"} />
            <Pill caption="Activity" value={activityLabel}
              tone={inferred ? "warn" : activity === "running" ? "ok" : activity === "idle" ? "neutral" : "muted"}
              title={inferred ? "Inferred from recent result-file writes, not native acquisition status" : undefined} />
          </div>
        </Section>
        <Section title="Run queue">
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            <Pill caption="Pending" value={queueKnown ? String(queueLength) : "Not observed"}
              tone={queueKnown ? "neutral" : "muted"} />
            <Pill caption="Current" value={currentTitle ?? "Not observed"}
              tone={currentTitle ? "neutral" : "muted"} />
          </div>
        </Section>
        <Section title="Latest result">
          <Pill caption="Result" value={resultName}
            tone={resultName === "Not observed" ? "muted" : "neutral"} />
          <p className="text-[11px] text-ink-subtle dark:text-slate-400">
            File updated: {age(lastResult.age_s ?? metrics.last_result_age?.value)} · not proof of run completion
          </p>
        </Section>
        <Section title="Software diagnostics">
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {diagnostic("chemstation_acquisition", "Acquisition app", { running: "Open" })}
            {diagnostic("chemstation_analysis", "Analysis app", { running: "Open" })}
            {diagnostic("data_service", "Data service", {}, true)}
            <div className={`min-w-0 rounded-md border px-2 py-1.5 sm:col-span-2 ${TONE_CLASSES.neutral}`}>
              <div className="flex items-center justify-between gap-2 text-[11px] text-ink-subtle dark:text-slate-400">
                <span className="uppercase tracking-wider">Results folder</span>
                <span>{components.data_path?.state === "available" ? "Readable" : "Access unknown or unavailable"}</span>
              </div>
              {resultsFolders.length ? resultsFolders.map((path, index) =>
                <p key={`${index}-${path}`} className="break-all font-mono text-xs text-ink dark:text-slate-100" title="Configured results folder watched by the monitor; not necessarily the active method's output folder">{path}</p>
              ) : <p className="text-xs text-ink-subtle">Path not available</p>}
            </div>
          </div>
          <p className="text-[11px] text-ink-subtle dark:text-slate-400">Open means the process is present; readable means the configured folder can be read. Neither establishes module readiness.</p>
        </Section>
      </div>
    </TileShell>
  );
}
