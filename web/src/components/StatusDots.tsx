import type { EquipmentSnapshot } from "@/types/api";
import {
  ACTIVITY_COLORS,
  ACTIVITY_META,
  STATE_COLORS,
  STATE_META,
  effectiveState,
  type ActivityName,
} from "@/lib/state-meta";

/**
 * One hover-labelled colour dot.
 *
 * The label is rendered in a styled bubble that appears immediately on hover
 * (a native `title` waits ~1 s and is easy to miss on a 10 px target) and is
 * also the element's `aria-label`, so colour is never the only channel. The
 * dot's hit area is deliberately larger than the dot itself.
 */
/** Where the hover bubble is drawn relative to the dot. `below` suits a row
 *  whose container doesn't clip; `right` keeps the bubble inside the row's
 *  own height, which is what the History → Uptime table needs (its platform
 *  groups are `overflow-hidden`, so a bubble below the last row is cut off). */
export type DotTooltipPlacement = "below" | "right";

const TOOLTIP_POSITION: Record<DotTooltipPlacement, string> = {
  below: "right-0 top-full mt-1",
  right: "left-full top-1/2 ml-1 -translate-y-1/2",
};

export function HoverDot({
  label,
  colour,
  pulse = false,
  placement = "below",
}: {
  label: string;
  colour: string;
  pulse?: boolean;
  placement?: DotTooltipPlacement;
}) {
  return (
    <span
      role="img"
      aria-label={label}
      className="group/dot relative inline-flex h-5 w-4 cursor-help items-center justify-center"
    >
      <span
        className={`inline-block h-2.5 w-2.5 rounded-full ring-2 ring-white dark:ring-slate-950 ${pulse ? "animate-pulse" : ""}`}
        style={{ backgroundColor: colour }}
      />
      <span
        className={`pointer-events-none invisible absolute z-50 whitespace-nowrap rounded-lg bg-slate-900 px-2.5 py-1.5 text-xs font-medium leading-none text-white opacity-0 shadow-lg transition-opacity group-hover/dot:visible group-hover/dot:opacity-100 dark:bg-slate-700 ${TOOLTIP_POSITION[placement]}`}
      >
        {label}
      </span>
    </span>
  );
}

/**
 * Current health + activity for one device (STATUS_SPEC v1.2 §2.3): two
 * independent colour dots, health first. The two axes answer different
 * questions and neither may hide the other — a degraded shaker mid-cycle
 * shows an orange health dot beside a live sky activity dot. Hover a dot for
 * that axis's state.
 *
 * Shared by the Overview equipment rows and the History → Uptime table so
 * both read as one system.
 */
export function StatusDots({
  snapshot,
  placement = "below",
}: {
  snapshot: EquipmentSnapshot;
  placement?: DotTooltipPlacement;
}) {
  const health = effectiveState(snapshot);
  const healthMeta = STATE_META[health] ?? STATE_META.unknown;
  const activity = (snapshot.activity ?? "unknown") as ActivityName;
  // ACTIVITY_META's `unknown` label is "—", which reads as nothing on hover.
  const activityLabel =
    activity === "unknown" ? "Unknown" : ACTIVITY_META[activity].label;
  return (
    <span className="inline-flex items-center">
      <HoverDot
        label={`Health: ${healthMeta.label}`}
        colour={STATE_COLORS[health] ?? STATE_COLORS.unknown}
        placement={placement}
      />
      <HoverDot
        label={`Activity: ${activityLabel}`}
        colour={ACTIVITY_COLORS[activity]}
        pulse={activity === "running"}
        placement={placement}
      />
    </span>
  );
}
