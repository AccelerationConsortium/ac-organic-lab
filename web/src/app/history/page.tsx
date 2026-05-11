"use client";

import { useState } from "react";
import { useEquipmentList } from "@/lib/use-equipment";
import {
  useAllUptime,
  useEquipmentEvents,
  useSensorHistory,
} from "@/lib/use-history";
import type { EquipmentSnapshot } from "@/types/api";
import type { SensorPoint } from "@/lib/history-api";

// ---------------------------------------------------------------------------
// Shared primitives
// ---------------------------------------------------------------------------

function SectionPill({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full px-3 py-1 text-sm font-medium transition-colors ${
        active
          ? "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300"
          : "text-ink-muted hover:text-ink dark:text-slate-400 dark:hover:text-slate-200"
      }`}
    >
      {label}
    </button>
  );
}

function EmptyState({ message, sub }: { message: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 p-8 text-center dark:border-slate-700">
      <p className="text-sm font-medium text-ink-muted dark:text-slate-400">{message}</p>
      {sub && <p className="mt-1 text-xs text-ink-subtle dark:text-slate-500">{sub}</p>}
    </div>
  );
}

function LoadingRow() {
  return (
    <div className="h-12 animate-pulse rounded-lg bg-slate-100 dark:bg-slate-800" />
  );
}

// ---------------------------------------------------------------------------
// State metadata — colour + label + description for every API state
// ---------------------------------------------------------------------------

type StateName =
  | "ready" | "busy" | "requires_init" | "degraded"
  | "dry_run" | "error" | "e_stop" | "unknown" | "unreachable";

// Hex colours used for bar segments and legend dots (inline styles — safe from Tailwind purge)
const STATE_COLORS: Record<StateName, string> = {
  ready:         "#10b981", // emerald-500
  busy:          "#0ea5e9", // sky-500
  requires_init: "#fbbf24", // amber-400
  degraded:      "#f97316", // orange-500
  dry_run:       "#8b5cf6", // violet-500
  error:         "#f43f5e", // rose-500
  e_stop:        "#b91c1c", // red-700
  unknown:       "#94a3b8", // slate-400
  unreachable:   "#fb7185", // rose-400
};

const STATE_META: Record<StateName, {
  label: string;
  dot: string;
  badge: string;
  desc: string;
}> = {
  ready:         { label: "Ready",        dot: "bg-emerald-500",  badge: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300", desc: "Idle and ready to accept commands." },
  busy:          { label: "Busy",         dot: "bg-sky-500",      badge: "bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300",                 desc: "Executing a protocol or operation." },
  requires_init: { label: "Needs Init",   dot: "bg-amber-400",    badge: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",         desc: "Requires initialization before use." },
  degraded:      { label: "Degraded",     dot: "bg-orange-500",   badge: "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300",     desc: "Reachable but operating in reduced capacity." },
  dry_run:       { label: "Dry Run",      dot: "bg-violet-500",   badge: "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300",     desc: "Simulating operations without physical actuation." },
  error:         { label: "Error",        dot: "bg-rose-500",     badge: "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300",             desc: "Device reported an internal error — check device logs." },
  e_stop:        { label: "E-Stop",       dot: "bg-red-700",      badge: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",                 desc: "Emergency stop active — physical inspection required." },
  unknown:       { label: "Unknown",      dot: "bg-slate-400",    badge: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",            desc: "Reachable but no clear state reported (mock adapter, startup) — or unobserved time before the aggregator started polling. Counted as up for uptime %." },
  unreachable:   { label: "Unreachable",  dot: "bg-rose-400",     badge: "bg-rose-50 text-rose-600 dark:bg-rose-900/20 dark:text-rose-400",             desc: "Aggregator cannot reach the device — counted as down. This is what 'offline' means here, not Unknown." },
};

function effectiveState(snap: EquipmentSnapshot): StateName {
  if (snap.fetch_error) return "unreachable";
  return (snap.status?.equipment_status as StateName) ?? "unknown";
}

function StateDot({ snap }: { snap: EquipmentSnapshot }) {
  const state = effectiveState(snap);
  const meta = STATE_META[state] ?? STATE_META.unknown;
  return (
    <span
      aria-label={meta.label}
      className="group/state-dot relative inline-flex h-5 w-5 items-center justify-center"
    >
      <span
        className="inline-block h-2.5 w-2.5 rounded-full ring-2 ring-white dark:ring-slate-950"
        style={{ backgroundColor: STATE_COLORS[state] ?? STATE_COLORS.unknown }}
      />
      <span className="pointer-events-none invisible absolute left-1/2 top-full z-50 mt-1 -translate-x-1/2 rounded-lg bg-slate-900 px-3 py-2 text-xs leading-relaxed text-white opacity-0 shadow-lg transition-opacity group-hover/state-dot:visible group-hover/state-dot:opacity-100 dark:bg-slate-700">
        {meta.label}
      </span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Platform label map
// ---------------------------------------------------------------------------

const PLATFORM_LABELS: Record<string, string> = {
  hte: "HTE Platform",
  lab: "Lab",
};

function platformLabel(p: string) {
  return PLATFORM_LABELS[p] ?? (p.charAt(0).toUpperCase() + p.slice(1));
}

// ---------------------------------------------------------------------------
// Segmented state timeline bar
// Bar fills 100% of its width, segments sized proportional to OBSERVED time
// (not the full window) so colors are visible even on a fresh install.
// ---------------------------------------------------------------------------

const STATE_ORDER: StateName[] = [
  "ready", "busy", "requires_init", "degraded", "dry_run",
  "error", "e_stop", "unknown", "unreachable",
];

function StateTimelineBar({
  statePcts,
  isPending,
}: {
  statePcts: Record<string, number> | undefined;
  isPending: boolean;
}) {
  if (isPending) {
    return <div className="h-2 animate-pulse rounded-full bg-slate-200 dark:bg-slate-700" />;
  }

  const entries: { state: StateName; pct: number }[] = [];
  for (const s of STATE_ORDER) {
    const v = statePcts?.[s] ?? 0;
    if (v > 0) entries.push({ state: s, pct: v });
  }
  // Append any states not in STATE_ORDER
  for (const [s, v] of Object.entries(statePcts ?? {})) {
    if (!(STATE_ORDER as string[]).includes(s) && v > 0)
      entries.push({ state: s as StateName, pct: v });
  }

  if (entries.length === 0) {
    return (
      <div className="h-2 w-full rounded-full bg-slate-200 dark:bg-slate-700" />
    );
  }

  // Normalize to 100% so segments are always visible regardless of window coverage
  const total = entries.reduce((acc, e) => acc + e.pct, 0);

  return (
    <div className="flex h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
      {entries.map(({ state, pct }) => {
        const meta = STATE_META[state] ?? STATE_META.unknown;
        const widthPct = (pct / total) * 100;
        return (
          <div
            key={state}
            title={`${meta.label}: ${pct.toFixed(1)}% of window`}
            className="h-2"
            style={{
              width: `${widthPct}%`,
              backgroundColor: STATE_COLORS[state] ?? STATE_COLORS.unknown,
            }}
          />
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SVG sparkline (no external deps)
// ---------------------------------------------------------------------------

function Sparkline({
  points,
  w = 120,
  h = 32,
}: {
  points: SensorPoint[];
  w?: number;
  h?: number;
}) {
  if (points.length < 2) return <span className="text-xs text-ink-subtle">—</span>;
  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const coords = points.map((p, i) => {
    const x = (i / (points.length - 1)) * w;
    const y = h - ((p.value - min) / range) * (h - 4) - 2;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      className="overflow-visible text-sky-500 dark:text-sky-400"
    >
      <polyline
        points={coords.join(" ")}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Uptime section
// ---------------------------------------------------------------------------

const UPTIME_WINDOWS = [
  { label: "24 h", days: 1 },
  { label: "7 d", days: 7 },
  { label: "30 d", days: 30 },
];

function UptimeWindowPicker({
  days,
  onChange,
}: {
  days: number;
  onChange: (days: number) => void;
}) {
  return (
    <div className="flex gap-1 rounded-lg border border-slate-200 bg-white p-1 dark:border-slate-700 dark:bg-slate-950/40">
      {UPTIME_WINDOWS.map((w) => (
        <button
          key={w.days}
          onClick={() => onChange(w.days)}
          className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
            days === w.days
              ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
              : "text-ink-muted hover:text-ink dark:text-slate-400 dark:hover:text-slate-200"
          }`}
        >
          {w.label}
        </button>
      ))}
    </div>
  );
}

// Single device row inside a platform group
function DeviceUptimeRow({
  snap,
  uptimeData,
  isPending,
  expanded,
  onToggle,
}: {
  snap: EquipmentSnapshot;
  uptimeData: Record<string, { uptime_pct: number | null; state_pcts: Record<string, number> }> | undefined;
  isPending: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  const summary = uptimeData?.[snap.id];
  const pct = summary?.uptime_pct ?? null;
  const pctColour =
    pct === null ? "text-ink-subtle dark:text-slate-500"
    : pct >= 95  ? "text-emerald-600 dark:text-emerald-400"
    : pct >= 80  ? "text-amber-600 dark:text-amber-400"
    :              "text-rose-600 dark:text-rose-400";

  return (
    <div>
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/50"
      >
        {/* Name */}
        <div className="w-44 min-w-0 shrink-0">
          <p className="truncate text-sm font-medium text-ink dark:text-slate-100">
            {snap.name}
          </p>
        </div>

        {/* State dot; legend already carries the text labels. */}
        <div className="w-10 shrink-0 text-center">
          <StateDot snap={snap} />
        </div>

        {/* Segmented bar */}
        <div className="flex-1">
          <StateTimelineBar statePcts={summary?.state_pcts} isPending={isPending} />
        </div>

        {/* Percentage */}
        <div className={`w-14 text-right text-sm font-semibold tabular-nums ${pctColour}`}>
          {isPending ? "…" : pct !== null ? `${pct}%` : "—"}
        </div>

        {/* Chevron */}
        <span className="ml-1 text-xs text-ink-subtle dark:text-slate-500">
          {expanded ? "▲" : "▼"}
        </span>
      </button>

      {expanded && <DeviceEventsList deviceId={snap.id} />}
    </div>
  );
}

// A titled card grouping all devices from one platform
function PlatformGroup({
  platform,
  snaps,
  uptimeData,
  isPending,
  days,
  onDaysChange,
}: {
  platform: string;
  snaps: EquipmentSnapshot[];
  uptimeData: Record<string, { uptime_pct: number | null; state_pcts: Record<string, number> }> | undefined;
  isPending: boolean;
  days: number;
  onDaysChange: (days: number) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const toggle = (id: string) => setExpanded((prev) => (prev === id ? null : id));

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800">
      {/* Platform header */}
      <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-4 py-2.5 dark:border-slate-800 dark:bg-slate-900/60">
        <h4 className="text-xs font-semibold uppercase tracking-widest text-ink-muted dark:text-slate-400">
          {platformLabel(platform)}
        </h4>
        <div className="flex items-center gap-3">
          <span className="text-xs text-ink-subtle dark:text-slate-500">
            {snaps.length} module{snaps.length !== 1 ? "s" : ""}
          </span>
          <UptimeWindowPicker days={days} onChange={onDaysChange} />
        </div>
      </div>

      {/* Column header */}
      <div className="flex items-center gap-3 border-b border-slate-100 px-4 py-1.5 dark:border-slate-800">
        <span className="w-44 shrink-0 text-[10px] font-medium uppercase tracking-wide text-ink-subtle dark:text-slate-500">Module</span>
        <span className="w-10 shrink-0 text-center text-[10px] font-medium uppercase tracking-wide text-ink-subtle dark:text-slate-500">State</span>
        <span className="flex-1 text-[10px] font-medium uppercase tracking-wide text-ink-subtle dark:text-slate-500">Uptime</span>
        <span className="w-14 text-right text-[10px] font-medium uppercase tracking-wide text-ink-subtle dark:text-slate-500">%</span>
        <span className="ml-1 w-3" />
      </div>

      <div className="divide-y divide-slate-100 dark:divide-slate-800">
        {snaps.map((snap) => (
          <DeviceUptimeRow
            key={snap.id}
            snap={snap}
            uptimeData={uptimeData}
            isPending={isPending}
            expanded={expanded === snap.id}
            onToggle={() => toggle(snap.id)}
          />
        ))}
      </div>
    </div>
  );
}

function UptimeSection({ snapshots }: { snapshots: EquipmentSnapshot[] }) {
  const [days, setDays] = useState(7);

  const { data: uptimeData, isPending } = useAllUptime(days);

  // Group by platform, preserving original order within each group
  const platforms: string[] = [];
  const byPlatform: Record<string, EquipmentSnapshot[]> = {};
  for (const snap of snapshots) {
    const p = snap.platform ?? "other";
    if (!byPlatform[p]) {
      byPlatform[p] = [];
      platforms.push(p);
    }
    byPlatform[p].push(snap);
  }

  return (
    <section className="flex flex-col gap-4">
      {snapshots.length === 0 ? (
        <EmptyState message="No devices found in registry." />
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {platforms.map((p) => (
            <PlatformGroup
              key={p}
              platform={p}
              snaps={byPlatform[p]}
              uptimeData={uptimeData?.devices}
              isPending={isPending}
              days={days}
              onDaysChange={setDays}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function DeviceEventsList({ deviceId }: { deviceId: string }) {
  const { data, isPending } = useEquipmentEvents(deviceId, 30);

  return (
    <div className="border-t border-slate-100 bg-slate-50 px-4 py-3 dark:border-slate-800 dark:bg-slate-900/50">
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-subtle dark:text-slate-500">
        Recent events
      </p>
      {isPending && <LoadingRow />}
      {data?.events.length === 0 && (
        <p className="text-xs text-ink-subtle dark:text-slate-500">No events recorded yet.</p>
      )}
      <ul className="flex flex-col gap-1">
        {data?.events.slice(0, 10).map((ev, i) => (
          <li key={i} className="flex items-start gap-3 text-xs">
            <span className="w-36 shrink-0 tabular-nums text-ink-subtle dark:text-slate-500">
              {new Date(ev.ts).toLocaleString()}
            </span>
            <span
              className={`rounded px-1.5 py-0.5 font-mono font-medium ${
                ev.event_type === "error"
                  ? "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300"
                  : ev.event_type === "state_transition"
                    ? "bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300"
                    : "bg-slate-100 text-ink dark:bg-slate-800 dark:text-slate-200"
              }`}
            >
              {ev.event_type}
            </span>
            {ev.from_state && ev.to_state && (
              <span className="text-ink-muted dark:text-slate-400">
                {ev.from_state} → {ev.to_state}
              </span>
            )}
            {ev.message && (
              <span className="truncate text-ink-muted dark:text-slate-400">{ev.message}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// State legend — compact sidebar, CSS tooltip on hover (no title attr)
// ---------------------------------------------------------------------------

function StateLegend() {
  return (
    <aside className="rounded-xl border border-slate-200 p-4 dark:border-slate-800">
      <p className="mb-3 text-[10px] font-semibold uppercase tracking-widest text-ink-subtle dark:text-slate-500">
        State Reference
      </p>
      <ul className="flex flex-col gap-2">
        {(Object.entries(STATE_META) as [StateName, typeof STATE_META[StateName]][]).map(
          ([key, meta]) => (
            <li key={key} className="group relative flex cursor-default items-center gap-2">
              {/* Dot — inline style so it's never purged */}
              <span
                className="inline-block h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: STATE_COLORS[key] }}
              />
              <span className="text-xs font-medium text-ink dark:text-slate-200">
                {meta.label}
              </span>

              {/* CSS tooltip — appears to the left of the sidebar */}
              <div className="pointer-events-none invisible absolute right-full top-1/2 z-50 mr-3 w-48 -translate-y-1/2 rounded-lg bg-slate-900 px-3 py-2 text-xs leading-relaxed text-white opacity-0 shadow-lg transition-opacity group-hover:visible group-hover:opacity-100 dark:bg-slate-700">
                {meta.desc}
                {/* arrow */}
                <span className="absolute right-0 top-1/2 -translate-y-1/2 translate-x-full border-4 border-transparent border-l-slate-900 dark:border-l-slate-700" />
              </div>
            </li>
          ),
        )}
      </ul>
      <p className="mt-4 text-[10px] leading-relaxed text-ink-subtle dark:text-slate-500">
        Hover a label for details.
        <br />
        Bar and uptime % reflect observed time only. Periods before tracking
        started aren't counted. Uptime % covers everything except{" "}
        <span className="font-medium">Unreachable</span>.
      </p>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Sensors section
// ---------------------------------------------------------------------------

const SENSOR_WINDOWS = [
  { label: "1 h", hours: 1 },
  { label: "6 h", hours: 6 },
  { label: "24 h", hours: 24 },
  { label: "7 d", hours: 168 },
];

const METRICS = [
  { key: "temperature_c", label: "Temperature", unit: "°C" },
  { key: "humidity_pct", label: "Humidity", unit: "%" },
  { key: "co2_ppm", label: "CO₂", unit: "ppm" },
];

function SensorCard({
  sensor,
  sinceHours,
}: {
  sensor: EquipmentSnapshot;
  sinceHours: number;
}) {
  const isMock = sensor.adapter === "mock";

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-slate-200 p-5 dark:border-slate-800">
      <header className="flex items-center justify-between">
        <div>
          <h4 className="text-sm font-semibold text-ink dark:text-slate-100">{sensor.name}</h4>
          <p className="font-mono text-xs text-ink-subtle dark:text-slate-500">{sensor.id}</p>
        </div>
        {isMock && (
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-ink-muted dark:bg-slate-800 dark:text-slate-400">
            mock
          </span>
        )}
      </header>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {METRICS.map((m) => (
          <SensorMetricChart
            key={m.key}
            sensorId={sensor.id}
            metric={m.key}
            label={m.label}
            unit={m.unit}
            sinceHours={sinceHours}
          />
        ))}
      </div>
    </div>
  );
}

function SensorMetricChart({
  sensorId,
  metric,
  label,
  unit,
  sinceHours,
}: {
  sensorId: string;
  metric: string;
  label: string;
  unit: string;
  sinceHours: number;
}) {
  const { data, isPending } = useSensorHistory(sensorId, metric, sinceHours);

  const latest = data?.readings.at(-1);
  const readings = data?.readings ?? [];

  return (
    <div className="flex flex-col gap-1 rounded-lg bg-slate-50 p-3 dark:bg-slate-800/50">
      <p className="text-xs font-medium text-ink-muted dark:text-slate-400">{label}</p>
      {isPending ? (
        <div className="h-8 animate-pulse rounded bg-slate-200 dark:bg-slate-700" />
      ) : readings.length > 0 ? (
        <>
          <p className="text-xl font-semibold tabular-nums text-ink dark:text-slate-100">
            {latest?.value.toFixed(1)}
            <span className="ml-1 text-sm font-normal text-ink-muted dark:text-slate-400">
              {unit}
            </span>
          </p>
          <Sparkline points={readings} />
        </>
      ) : (
        <p className="text-sm text-ink-subtle dark:text-slate-500">No data</p>
      )}
    </div>
  );
}

function SensorsSection({ sensors }: { sensors: EquipmentSnapshot[] }) {
  const [hours, setHours] = useState(1);

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-base font-semibold text-ink dark:text-slate-100">
          Environmental Sensors
        </h3>
        <div className="flex gap-1 rounded-lg border border-slate-200 p-1 dark:border-slate-700">
          {SENSOR_WINDOWS.map((w) => (
            <button
              key={w.hours}
              onClick={() => setHours(w.hours)}
              className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
                hours === w.hours
                  ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                  : "text-ink-muted hover:text-ink dark:text-slate-400 dark:hover:text-slate-200"
              }`}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      {sensors.length === 0 ? (
        <EmptyState
          message="No environmental sensors in registry."
          sub="Add entries with kind: environmental_sensor in equipment.yaml."
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {sensors.map((s) => (
            <SensorCard key={s.id} sensor={s} sinceHours={hours} />
          ))}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// History page
// ---------------------------------------------------------------------------

type Section = "uptime" | "sensors";

export default function HistoryPage() {
  const [section, setSection] = useState<Section>("uptime");
  const { data: equipmentData, isPending } = useEquipmentList(0); // no refetch on this page

  if (isPending) {
    return <p className="text-sm text-ink-muted dark:text-slate-400">Loading…</p>;
  }

  const all = equipmentData?.equipment ?? [];
  const sensors = all.filter((s) => s.kind === "environmental_sensor");
  // Everything except env sensors goes into the uptime table.
  const modules = all.filter((s) => s.kind !== "environmental_sensor");

  return (
    <div className="flex flex-col gap-6">
      <header>
        <p className="text-sm text-ink-muted dark:text-slate-400">
          Module uptime and environmental sensor trends. Refreshes every 30 s.
        </p>
      </header>

      {/* Section tabs */}
      <div className="flex gap-1 border-b border-slate-200 pb-2 dark:border-slate-800">
        {(
          [
            { id: "uptime", label: `Uptime (${modules.length})` },
            { id: "sensors", label: `Sensors (${sensors.length})` },
          ] as { id: Section; label: string }[]
        ).map((tab) => (
          <SectionPill
            key={tab.id}
            label={tab.label}
            active={section === tab.id}
            onClick={() => setSection(tab.id)}
          />
        ))}
      </div>

      {/* Main content + right legend sidebar */}
      <div className="flex items-start gap-6">
        <div className="min-w-0 flex-1">
          {section === "uptime" && <UptimeSection snapshots={modules} />}
          {section === "sensors" && <SensorsSection sensors={sensors} />}
        </div>
        <div className="w-44 shrink-0">
          <StateLegend />
        </div>
      </div>
    </div>
  );
}
