"use client";

import { useState } from "react";
import { useEquipmentList } from "@/lib/use-equipment";
import {
  useAllUptime,
  useDeviceUptime,
  useEquipmentEvents,
  useSensorHistory,
  useRuns,
  useWellResults,
} from "@/lib/use-history";
import type { EquipmentSnapshot } from "@/types/api";
import type { RunRecord, SensorPoint, WellResult } from "@/lib/history-api";

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
// Uptime bar (CSS width, colour by %)
// ---------------------------------------------------------------------------

function UptimeBar({ pct }: { pct: number | null }) {
  if (pct === null) {
    return (
      <div className="flex h-2 w-full rounded-full bg-slate-200 dark:bg-slate-700">
        <div className="h-2 w-full rounded-full bg-slate-300 dark:bg-slate-600" />
      </div>
    );
  }
  const colour =
    pct >= 95
      ? "bg-emerald-500"
      : pct >= 80
        ? "bg-amber-400"
        : "bg-rose-500";
  return (
    <div className="flex h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
      <div
        className={`h-2 rounded-full transition-all ${colour}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function uptimeColour(pct: number | null) {
  if (pct === null) return "text-ink-subtle dark:text-slate-500";
  if (pct >= 95) return "text-emerald-600 dark:text-emerald-400";
  if (pct >= 80) return "text-amber-600 dark:text-amber-400";
  return "text-rose-600 dark:text-rose-400";
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

function UptimeSection({ snapshots }: { snapshots: EquipmentSnapshot[] }) {
  const [days, setDays] = useState(7);
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data: uptimeData, isPending } = useAllUptime(days);

  const toggle = (id: string) => setExpanded((prev) => (prev === id ? null : id));

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-base font-semibold text-ink dark:text-slate-100">
          Module Uptime
        </h3>
        <div className="flex gap-1 rounded-lg border border-slate-200 p-1 dark:border-slate-700">
          {UPTIME_WINDOWS.map((w) => (
            <button
              key={w.days}
              onClick={() => setDays(w.days)}
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
      </div>

      <div className="flex flex-col divide-y divide-slate-100 rounded-xl border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
        {snapshots.map((snap) => {
          const summary = uptimeData?.devices[snap.id];
          const pct = summary?.uptime_pct ?? null;

          return (
            <div key={snap.id}>
              <button
                onClick={() => toggle(snap.id)}
                className="flex w-full items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/50"
              >
                {/* Name + id */}
                <div className="w-48 min-w-0 shrink-0">
                  <p className="truncate text-sm font-medium text-ink dark:text-slate-100">
                    {snap.name}
                  </p>
                  <p className="truncate font-mono text-xs text-ink-subtle dark:text-slate-500">
                    {snap.id}
                  </p>
                </div>

                {/* Bar */}
                <div className="flex-1">
                  {isPending ? (
                    <div className="h-2 animate-pulse rounded-full bg-slate-200 dark:bg-slate-700" />
                  ) : (
                    <UptimeBar pct={pct} />
                  )}
                </div>

                {/* Percentage */}
                <div className={`w-14 text-right text-sm font-semibold tabular-nums ${uptimeColour(pct)}`}>
                  {isPending ? "…" : pct !== null ? `${pct}%` : "—"}
                </div>

                {/* Chevron */}
                <span className="ml-1 text-xs text-ink-subtle dark:text-slate-500">
                  {expanded === snap.id ? "▲" : "▼"}
                </span>
              </button>

              {expanded === snap.id && (
                <DeviceEventsList deviceId={snap.id} />
              )}
            </div>
          );
        })}

        {snapshots.length === 0 && (
          <div className="p-6">
            <EmptyState message="No devices found in registry." />
          </div>
        )}
      </div>

      {uptimeData && Object.keys(uptimeData.devices).length === 0 && (
        <p className="text-xs text-ink-subtle dark:text-slate-500">
          Uptime data accumulates as the aggregator observes reachability changes.
          Check back after the first poll cycle (~60 s).
        </p>
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

  if (isMock) {
    return (
      <div className="flex flex-col gap-3 rounded-xl border border-dashed border-slate-300 p-5 dark:border-slate-700">
        <header className="flex items-center justify-between">
          <div>
            <h4 className="text-sm font-semibold text-ink dark:text-slate-100">{sensor.name}</h4>
            <p className="font-mono text-xs text-ink-subtle dark:text-slate-500">{sensor.id}</p>
          </div>
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-ink-muted dark:bg-slate-800 dark:text-slate-400">
            mock
          </span>
        </header>
        <p className="text-xs text-ink-muted dark:text-slate-400">
          No real readings yet. In{" "}
          <span className="font-mono">equipment.yaml</span> change{" "}
          <span className="font-mono">adapter: mock</span> →{" "}
          <span className="font-mono">adapter: http</span> with a{" "}
          <span className="font-mono">base_url</span> once the{" "}
          <span className="font-mono">sense-every-zone</span> service is deployed.
          Readings will appear here automatically.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-slate-200 p-5 dark:border-slate-800">
      <h4 className="text-sm font-semibold text-ink dark:text-slate-100">{sensor.name}</h4>
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
// Runs section
// ---------------------------------------------------------------------------

const RUN_STATUS_STYLES: Record<string, string> = {
  complete: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300",
  failed: "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300",
  aborted: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",
  in_progress: "bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300",
};

function RunRow({ run }: { run: RunRecord }) {
  const [showWells, setShowWells] = useState(false);
  const { data: wellData, isPending } = useWellResults(run.id, showWells);

  const convergePct =
    run.n_wells > 0 ? Math.round((run.n_converged / run.n_wells) * 100) : null;

  return (
    <>
      <tr
        className="cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/50"
        onClick={() => setShowWells((v) => !v)}
      >
        <td className="py-2.5 pl-4 pr-3 font-mono text-xs text-ink-subtle dark:text-slate-500">
          {new Date(run.started_at).toLocaleString()}
        </td>
        <td className="px-3 py-2.5 text-xs text-ink-muted dark:text-slate-400">
          {run.plate_id ?? "—"}
        </td>
        <td className="px-3 py-2.5 text-xs text-ink-muted dark:text-slate-400">
          {run.device_id}
        </td>
        <td className="px-3 py-2.5 text-xs tabular-nums text-ink dark:text-slate-200">
          {run.n_converged}/{run.n_wells}
          {convergePct !== null && (
            <span className="ml-1 text-ink-subtle dark:text-slate-500">
              ({convergePct}%)
            </span>
          )}
        </td>
        <td className="px-3 py-2.5 pr-4 text-right">
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${RUN_STATUS_STYLES[run.status] ?? ""}`}
          >
            {run.status}
          </span>
        </td>
      </tr>

      {showWells && (
        <tr>
          <td colSpan={5} className="bg-slate-50 p-0 dark:bg-slate-900/50">
        <WellHeatmap run={run} wells={(wellData?.wells ?? []) as WellResult[]} isPending={isPending} />
          </td>
        </tr>
      )}
    </>
  );
}

function WellHeatmap({
  run,
  wells,
  isPending,
}: {
  run: RunRecord;
  wells: WellResult[];
  isPending: boolean;
}) {
  const ROWS = ["A", "B", "C", "D", "E", "F", "G", "H"];
  const COLS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];

  const byWell = new Map(wells.map((w) => [w.well, w]));

  const cellColour = (wellId: string) => {
    const w = byWell.get(wellId);
    if (!w) return "bg-slate-100 dark:bg-slate-800";
    if (!w.converged) return "bg-rose-200 dark:bg-rose-900/50";
    if (w.actual_mg === null || run.target_mg === null) return "bg-slate-200 dark:bg-slate-700";
    const ratio = w.actual_mg / run.target_mg;
    if (ratio >= 0.95 && ratio <= 1.05) return "bg-emerald-400 dark:bg-emerald-600";
    if (ratio >= 0.85 && ratio <= 1.15) return "bg-amber-300 dark:bg-amber-600";
    return "bg-rose-300 dark:bg-rose-700";
  };

  return (
    <div className="px-4 py-3">
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-subtle dark:text-slate-500">
        Well results — hover for details
      </p>
      {isPending ? (
        <div className="h-24 animate-pulse rounded bg-slate-200 dark:bg-slate-700" />
      ) : (
        <>
          <div className="inline-grid gap-0.5" style={{ gridTemplateColumns: `auto repeat(12, 1.5rem)` }}>
            {/* column headers */}
            <div />
            {COLS.map((c) => (
              <div key={c} className="text-center font-mono text-[9px] text-ink-subtle dark:text-slate-500">
                {c}
              </div>
            ))}
            {/* rows */}
            {ROWS.map((row) => (
              <>
                <div
                  key={row}
                  className="flex items-center justify-end pr-1 font-mono text-[9px] text-ink-subtle dark:text-slate-500"
                >
                  {row}
                </div>
                {COLS.map((col) => {
                  const wellId = `${row}${col}`;
                  const w = byWell.get(wellId);
                  return (
                    <div
                      key={wellId}
                      title={
                        w
                          ? `${wellId}: ${w.actual_mg?.toFixed(2) ?? "?"} mg (target ${run.target_mg} mg, ${w.converged ? "✓" : "✗"})`
                          : wellId
                      }
                      className={`h-5 w-6 rounded-sm ${cellColour(wellId)}`}
                    />
                  );
                })}
              </>
            ))}
          </div>
          <div className="mt-2 flex gap-3 text-[10px] text-ink-subtle dark:text-slate-500">
            <span className="flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded-sm bg-emerald-400 dark:bg-emerald-600" />
              Within ±5%
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded-sm bg-amber-300 dark:bg-amber-600" />
              ±5–15%
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded-sm bg-rose-300 dark:bg-rose-700" />
              &gt;±15% or unconverged
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded-sm bg-slate-100 dark:bg-slate-800" />
              Not dosed
            </span>
          </div>
        </>
      )}
    </div>
  );
}

function RunsSection() {
  const { data, isPending } = useRuns(20);

  return (
    <section className="flex flex-col gap-4">
      <h3 className="text-base font-semibold text-ink dark:text-slate-100">
        Dosing Runs
      </h3>

      {isPending && (
        <div className="flex flex-col gap-2">
          {[...Array(3)].map((_, i) => <LoadingRow key={i} />)}
        </div>
      )}

      {!isPending && data?.runs.length === 0 && (
        <EmptyState
          message="No dosing runs recorded yet."
          sub="Runs appear here when workflow scripts call POST /api/ingest/runs after a plate is dosed."
        />
      )}

      {!isPending && data && data.runs.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
          <table className="min-w-full divide-y divide-slate-100 dark:divide-slate-800">
            <thead>
              <tr className="text-left text-xs font-medium uppercase tracking-wide text-ink-subtle dark:text-slate-500">
                <th className="py-2 pl-4 pr-3">Started</th>
                <th className="px-3 py-2">Plate</th>
                <th className="px-3 py-2">Device</th>
                <th className="px-3 py-2">Wells</th>
                <th className="px-3 py-2 pr-4 text-right">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {data.runs.map((run) => (
                <RunRow key={run.id} run={run} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// History page
// ---------------------------------------------------------------------------

type Section = "uptime" | "sensors" | "runs";

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
        <h2 className="text-lg font-semibold text-ink dark:text-slate-100">History</h2>
        <p className="mt-0.5 text-sm text-ink-muted dark:text-slate-400">
          Module uptime, environmental trends, and dosing run records. Refreshes every 30 s.
        </p>
      </header>

      {/* Section tabs */}
      <div className="flex gap-1 border-b border-slate-200 pb-2 dark:border-slate-800">
        {(
          [
            { id: "uptime", label: `Uptime (${modules.length})` },
            { id: "sensors", label: `Sensors (${sensors.length})` },
            { id: "runs", label: "Dosing Runs" },
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

      {section === "uptime" && <UptimeSection snapshots={modules} />}
      {section === "sensors" && <SensorsSection sensors={sensors} />}
      {section === "runs" && <RunsSection />}
    </div>
  );
}
