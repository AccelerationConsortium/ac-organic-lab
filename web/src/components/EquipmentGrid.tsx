import type { EquipmentSnapshot } from "@/types/api";
import { CameraTile } from "./CameraTile";
import { EquipmentStatusCard } from "./EquipmentStatusCard";
import { FumeHoodTile } from "./FumeHoodTile";
import { PlateSealerTile } from "./PlateSealerTile";
import { PowerStripTile } from "./PowerStripTile";
import { PressTile } from "./PressTile";
import { ShakerTile } from "./ShakerTile";

/**
 * Renders equipment cards on a 4-column CSS grid driven by `tile.{w,h}` in
 * `equipment.yaml`.
 *
 *   - lg+  : 4 columns, each row is a fixed 220px so `tile.h` translates to
 *            visible height (a 2×2 is exactly twice as tall as a 2×1 plus the
 *            gap between rows). Cards `overflow-hidden` to keep the tile look.
 *            Camera tiles want more vertical room than the standard 220 px
 *            row gives them - express that in the YAML by bumping `tile.h`
 *            (`{ w: 4, h: 4 }` is what the HTE camera uses to land as a
 *            full-width 880 px hero at the top of the grid).
 *   - sm   : 2 columns, content-driven heights, col-span capped at 2.
 *   - <sm  : 1 column, all tiles full-width and content-tall.
 *
 * The renderer dispatches on `kind`: cameras get the full `CameraTile`
 * (live video, PTZ pad, presets, privacy/streaming toggles); everything
 * else uses the generic `EquipmentStatusCard`. Adding a new specialised
 * tile type in the future is a matter of growing this dispatch.
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
            className="h-full"
            // Browsers cap span values at the available column count, so
            // span:4 on a 2-col grid just becomes full-width.
            style={{ gridColumn: `span ${w}`, gridRow: `span ${h}` }}
          >
            {snapshot.kind === "camera" ? (
              <CameraTile snapshot={snapshot} />
            ) : snapshot.kind === "power_strip" || snapshot.kind === "smart_plug" ? (
              <PowerStripTile snapshot={snapshot} />
            ) : snapshot.kind === "fume_hood" ? (
              <FumeHoodTile snapshot={snapshot} />
            ) : snapshot.kind === "plate_sealer" ? (
              <PlateSealerTile snapshot={snapshot} />
            ) : snapshot.kind === "press" ? (
              <PressTile snapshot={snapshot} />
            ) : snapshot.kind === "shaker" ? (
              <ShakerTile snapshot={snapshot} />
            ) : (
              <EquipmentStatusCard snapshot={snapshot} />
            )}
          </div>
        );
      })}
    </div>
  );
}
