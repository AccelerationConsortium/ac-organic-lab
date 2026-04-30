"use client";

import type { EquipmentSnapshot, MetricValue } from "@/types/api";

const METRIC_ORDER = ["temperature", "humidity", "o2", "voc"] as const;
const METRIC_LABEL: Record<(typeof METRIC_ORDER)[number], string> = {
  temperature: "T",
  humidity: "RH",
  o2: "O\u2082",
  voc: "VOC",
};

function formatTempShort(metric: MetricValue | undefined): string {
  if (!metric || metric.value == null) return "—";
  if (typeof metric.value !== "number") return String(metric.value);
  return `${metric.value.toFixed(1)}°`;
}

function formatMetric(metric: MetricValue | undefined): string {
  if (!metric || metric.value == null) return "—";
  if (typeof metric.value === "number") {
    const value = Number.isInteger(metric.value)
      ? metric.value.toString()
      : metric.value.toFixed(1);
    return metric.unit ? `${value} ${metric.unit}` : value;
  }
  return String(metric.value);
}

function markerStateClasses(snapshot: EquipmentSnapshot): {
  ring: string;
  dot: string;
} {
  const state = snapshot.status.equipment_status;
  if (state === "ready" || state === "busy") {
    return {
      ring: "ring-emerald-300 dark:ring-emerald-800",
      dot: "bg-emerald-500 dark:bg-emerald-400",
    };
  }
  if (state === "dry_run") {
    return {
      ring: "ring-violet-300 dark:ring-violet-800",
      dot: "bg-violet-500 dark:bg-violet-400",
    };
  }
  if (state === "error" || state === "e_stop") {
    return {
      ring: "ring-rose-300 dark:ring-rose-800",
      dot: "bg-rose-500 dark:bg-rose-400",
    };
  }
  if (state === "degraded" || state === "requires_init") {
    return {
      ring: "ring-amber-300 dark:ring-amber-800",
      dot: "bg-amber-500 dark:bg-amber-400",
    };
  }
  return {
    ring: "ring-slate-300 dark:ring-slate-700",
    dot: "bg-slate-400 dark:bg-slate-500",
  };
}

function SensorMarker({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const loc = snapshot.location;
  if (!loc) return null;

  const metrics = snapshot.status.metrics ?? {};
  const styles = markerStateClasses(snapshot);

  return (
    <div
      className="group absolute -translate-x-1/2 -translate-y-1/2 transform"
      style={{ left: `${loc.x}%`, top: `${loc.y}%` }}
    >
      {/* Compact pill always visible */}
      <div
        className={`flex items-center gap-1 rounded-full border border-slate-200 bg-white/95 px-2 py-0.5 text-[10px] font-medium shadow-sm ring-1 backdrop-blur dark:border-slate-700 dark:bg-slate-900/95 ${styles.ring}`}
      >
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${styles.dot}`}
          aria-hidden
        />
        <span className="font-mono text-ink dark:text-slate-200">
          {formatTempShort(metrics.temperature)}
        </span>
      </div>

      {/* Tooltip on hover/focus */}
      <div
        className="pointer-events-none absolute left-1/2 top-full z-10 mt-1.5 w-44 -translate-x-1/2 rounded-lg border border-slate-200 bg-white p-2.5 opacity-0 shadow-lg transition-opacity duration-150 group-hover:pointer-events-auto group-hover:opacity-100 dark:border-slate-700 dark:bg-slate-900"
        role="tooltip"
      >
        <div className="mb-1.5 truncate text-xs font-semibold text-ink dark:text-slate-100">
          {loc.label ?? snapshot.name}
        </div>
        <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[11px] leading-tight">
          {METRIC_ORDER.map((key) => (
            <div key={key} className="flex items-baseline gap-1">
              <dt className="text-ink-subtle dark:text-slate-500">
                {METRIC_LABEL[key]}
              </dt>
              <dd className="font-mono text-ink dark:text-slate-200">
                {formatMetric(metrics[key])}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

/**
 * Stylized lab floorplan, rotated 90° clockwise from the building plan so that
 * north is up. Four zones in a 100×100 normalised space:
 *
 *  +------------------+------------------+
 *  |     Stairs       |   Sample Prep    |  y  0..25
 *  |  (greyed out)    |                  |
 *  +------------------+------------------+
 *  |             Storage                 |  y 25..50
 *  +-------------------------------------+
 *  |                                     |
 *  |             Lab 499                 |  y 50..100
 *  |            (main lab)               |
 *  +-------------------------------------+
 *
 *  N is up; W is left; E is right; S is down.
 */
function FloorPlan() {
  const stroke = "currentColor";
  return (
    <svg
      className="absolute inset-0 h-full w-full text-slate-300 dark:text-slate-700"
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      aria-hidden
    >
      <defs>
        <pattern
          id="stairs-hatch"
          width="2"
          height="2"
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(45)"
        >
          <line
            x1="0"
            y1="0"
            x2="0"
            y2="2"
            stroke="currentColor"
            strokeWidth="0.6"
            opacity="0.35"
          />
        </pattern>
      </defs>

      {/* Greyed-out stairs zone (top-left quadrant) */}
      <rect
        x="0"
        y="0"
        width="50"
        height="25"
        fill="url(#stairs-hatch)"
        opacity="0.6"
      />
      {/* Stair-tread lines (vertical strokes after rotation) */}
      {[8, 16, 24, 32, 40].map((x) => (
        <line
          key={x}
          x1={x}
          y1="3"
          x2={x}
          y2="22"
          stroke={stroke}
          strokeWidth="0.25"
          opacity="0.6"
        />
      ))}

      {/* Outer wall */}
      <rect
        x="0.5"
        y="0.5"
        width="99"
        height="99"
        rx="0.8"
        fill="none"
        stroke={stroke}
        strokeWidth="0.5"
      />

      {/* Zone divisions */}
      {/* Top quarter: stairs | sample prep */}
      <line x1="50" y1="0.5" x2="50" y2="25" stroke={stroke} strokeWidth="0.4" />
      {/* Top quarter / storage */}
      <line x1="0.5" y1="25" x2="99.5" y2="25" stroke={stroke} strokeWidth="0.4" />
      {/* Storage / lab 499 */}
      <line x1="0.5" y1="50" x2="99.5" y2="50" stroke={stroke} strokeWidth="0.4" />
    </svg>
  );
}

function ZoneLabels() {
  const labelClass =
    "absolute text-[10px] font-medium uppercase tracking-wider text-ink-subtle dark:text-slate-500 pointer-events-none select-none";
  return (
    <>
      <span
        className={labelClass}
        style={{ left: "2%", top: "2%", opacity: 0.7 }}
      >
        Stairs
      </span>
      <span className={labelClass} style={{ left: "52%", top: "2%" }}>
        Sample Prep
      </span>
      <span className={labelClass} style={{ left: "2%", top: "26.5%" }}>
        Storage
      </span>
      <span className={labelClass} style={{ left: "2%", top: "51.5%" }}>
        Lab 499
      </span>
    </>
  );
}

function CompassRose() {
  return (
    <div
      className="pointer-events-none absolute right-2 top-2 z-10 flex flex-col items-center rounded border border-slate-200 bg-white/85 px-1.5 py-0.5 text-[10px] font-semibold text-ink-subtle shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/85 dark:text-slate-400"
      aria-label="North is up"
    >
      <span aria-hidden>↑</span>
      <span>N</span>
    </div>
  );
}

export function LabMap({ sensors }: { sensors: EquipmentSnapshot[] }) {
  return (
    <div className="relative w-full overflow-hidden rounded-xl border border-slate-200 bg-gradient-to-br from-slate-50 to-slate-100 dark:border-slate-800 dark:from-slate-900 dark:to-slate-950">
      {/* aspect-square keeps the 100×100 SVG comfortably sized in a half-width
          column. preserveAspectRatio="none" stretches the floorplan to fill. */}
      <div className="relative aspect-square w-full min-h-[320px]">
        <FloorPlan />
        <ZoneLabels />
        <CompassRose />
        {sensors.map((s) => (
          <SensorMarker key={s.id} snapshot={s} />
        ))}
      </div>
    </div>
  );
}
