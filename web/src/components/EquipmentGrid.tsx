import type { EquipmentSnapshot } from "@/types/api";
import { isMonitoringOnly } from "@/lib/tile-policy";
import { CameraTile } from "./CameraTile";
import { XprBalanceTile } from "./XprBalanceTile";
import { EasyMaxTile } from "./EasyMaxTile";
import { LumastirTile } from "./LumastirTile";
import { EquipmentStatusCard } from "./EquipmentStatusCard";
import { FumeHoodTile } from "./FumeHoodTile";
import { HplcTile } from "./HplcTile";
import { LiquidHandlerTile } from "./LiquidHandlerTile";
import { PlateReaderTile } from "./PlateReaderTile";
import { PlateSealerTile } from "./PlateSealerTile";
import { PlateStackerTile } from "./PlateStackerTile";
import { PowerStripTile } from "./PowerStripTile";
import { PressTile } from "./PressTile";
import { RobotArmTile } from "./RobotArmTile";
import { ShakerTile } from "./ShakerTile";
import { SolidDoserTile } from "./SolidDoserTile";

/**
 * Renders equipment cards on a 4-column CSS grid driven by `tile.{w,h}` in
 * `equipment.yaml`.
 *
 *   - lg+  : 4 columns with a 220px minimum logical row height.
 *            Half-row tracks support tile heights in increments of 0.5.
 *   - sm   : 2 columns, content-driven heights, col-span capped at 2.
 *   - <sm  : 1 column, all tiles full-width and content-tall.
 *
 * The renderer dispatches on `kind`: cameras get the full `CameraTile`
 * (live video, PTZ pad, presets, privacy/streaming toggles); everything
 * else uses the generic `EquipmentStatusCard`. Adding a new specialised
 * tile type in the future is a matter of growing this dispatch.
 *
 * One exception precedes the kind dispatch: a device whose envelope says
 * `details.monitoring_only: true` always gets the generic card. The
 * kind-specific tiles carry that kind's *control* surface — the xArm tile's
 * STOP / CLEAR / INIT and "Open control panel", the OT-2 tile's panel link
 * and deck layout — and a read-only observer of a UR arm or a Flex (the
 * Gibbie bench, `sdl2-gibbie-server`) has none of it: every one of those
 * affordances would 404. The generic card shows what such a device does
 * publish — state, message, components, metrics — and nothing it cannot do.
 */
const ROW_HEIGHT_PX = 220;
const GRID_GAP_PX = 12;
const HALF_ROW_HEIGHT_PX = (ROW_HEIGHT_PX - GRID_GAP_PX) / 2;

export function EquipmentGrid({ snapshots }: { snapshots: EquipmentSnapshot[] }) {
  if (snapshots.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-ink-subtle dark:border-slate-700 dark:text-slate-300">
        No equipment registered.
      </p>
    );
  }
  return (
    <div
      // lg+: four equal stretchy columns filling the content container, so a
      // standard 2-wide tile is exactly half the container — the same width
      // (and left/right alignment) as an Overview platform card, edge-to-edge
      // with the page heading. (Columns were previously capped at 262px,
      // which left the grid ~120px short of the container's right edge.)
      className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
      // Two half-row tracks plus their gap preserve the 220px module.
      // Tracks can still grow when controls need more room.
      // `dense` packing backfills the holes a taller tile leaves beside a
      // shorter one, so short tiles snap up into the gaps instead of the
      // grid leaving empty cells until the next full row.
      style={{
        gridAutoRows: `minmax(${HALF_ROW_HEIGHT_PX}px, auto)`,
        gridAutoFlow: "row dense",
      }}
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
            style={{
              gridColumn: `span ${w}`,
              gridRow: `span ${h * 2}`,
              // Neighboring equipment can grow the shared grid tracks. Keep
              // camera heights consistent instead of stretching their video.
              ...(snapshot.kind === "camera" ? {
                height: h * (ROW_HEIGHT_PX + GRID_GAP_PX) - GRID_GAP_PX,
                alignSelf: "start",
              } : {}),
            }}
          >
            {isMonitoringOnly(snapshot) ? (
              <EquipmentStatusCard snapshot={snapshot} />
            ) : ["lle_xpr_balance", "gibbie_balance", "gibbie_xpr_balance"].includes(snapshot.id) ? (
              <XprBalanceTile snapshot={snapshot} />
            ) : snapshot.id === "lumastir" ? (
              <LumastirTile snapshot={snapshot} />
            ) : snapshot.id === "lle_easymax" ? (
              <EasyMaxTile snapshot={snapshot} />
            ) : snapshot.kind === "camera" ? (
              <CameraTile snapshot={snapshot} />
            ) : snapshot.kind === "power_strip" || snapshot.kind === "smart_plug" ? (
              <PowerStripTile snapshot={snapshot} />
            ) : snapshot.kind === "fume_hood" ? (
              <FumeHoodTile snapshot={snapshot} />
            ) : snapshot.kind === "plate_reader" ? (
              <PlateReaderTile snapshot={snapshot} />
            ) : snapshot.kind === "plate_sealer" ? (
              <PlateSealerTile snapshot={snapshot} />
            ) : snapshot.kind === "plate_stacker" ? (
              <PlateStackerTile snapshot={snapshot} />
            ) : snapshot.kind === "press" ? (
              <PressTile snapshot={snapshot} />
            ) : snapshot.kind === "shaker" ? (
              <ShakerTile snapshot={snapshot} />
            ) : snapshot.kind === "robot_arm" ? (
              <RobotArmTile snapshot={snapshot} />
            ) : snapshot.kind === "liquid_handler" ? (
              <LiquidHandlerTile snapshot={snapshot} />
            ) : snapshot.kind === "hplc" ? (
              <HplcTile snapshot={snapshot} />
            ) : snapshot.kind === "solid_doser" ? (
              <SolidDoserTile snapshot={snapshot} />
            ) : (
              <EquipmentStatusCard snapshot={snapshot} />
            )}
          </div>
        );
      })}
    </div>
  );
}
