import type { EquipmentSnapshot } from "@/types/api";
import { EquipmentStatusCard } from "./EquipmentStatusCard";

/**
 * Renders equipment cards on a 4-column CSS grid driven by `tile.{w,h}` in
 * `equipment.yaml`.
 *
 *   - lg+  : 4 columns, each row is a fixed 220px so `tile.h` translates to
 *            visible height (a 2×2 is exactly twice as tall as a 2×1 plus the
 *            gap between rows). Cards `overflow-hidden` to keep the tile look.
 *   - sm   : 2 columns, content-driven heights, col-span capped at 2.
 *   - <sm  : 1 column, all tiles full-width and content-tall.
 */
const ROW_HEIGHT_PX = 220;

export function EquipmentGrid({ snapshots }: { snapshots: EquipmentSnapshot[] }) {
  if (snapshots.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-ink-subtle dark:border-slate-700 dark:text-slate-400">
        No equipment registered.
      </p>
    );
  }
  return (
    <div
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
      style={{ gridAutoRows: `${ROW_HEIGHT_PX}px` }}
    >
      {snapshots.map((snapshot) => {
        const w = snapshot.tile?.w ?? 2;
        const h = snapshot.tile?.h ?? 1;
        return (
          <div
            key={snapshot.id}
            // Browsers cap span values at the available column count, so
            // span:4 on a 2-col grid just becomes full-width.
            style={{ gridColumn: `span ${w}`, gridRow: `span ${h}` }}
          >
            <EquipmentStatusCard snapshot={snapshot} />
          </div>
        );
      })}
    </div>
  );
}
