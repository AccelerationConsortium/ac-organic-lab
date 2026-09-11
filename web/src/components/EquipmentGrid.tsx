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
 * Independent stacks keep a tall control tile from adding blank space below
 * its neighbors. Registry heights balance the columns without moving mounted
 * controls between columns as live status content changes. Cameras retain a
 * fixed height; other cards can grow to fit their controls.
 */
const ROW_HEIGHT_PX = 220;
const GRID_GAP_PX = 12;

function tileHeight(snapshot: EquipmentSnapshot) {
  return (snapshot.tile?.h ?? 1) * (ROW_HEIGHT_PX + GRID_GAP_PX) - GRID_GAP_PX;
}

function tileStacks(snapshots: EquipmentSnapshot[]) {
  const groups: EquipmentSnapshot[][][] = [];
  let columns: EquipmentSnapshot[][] = [[], []];
  let heights = [0, 0];
  for (const snapshot of snapshots) {
    if ((snapshot.tile?.w ?? 2) >= 4) {
      if (columns.some((column) => column.length)) groups.push(columns);
      groups.push([[snapshot]]);
      columns = [[], []];
      heights = [0, 0];
    } else {
      const column = heights[0] <= heights[1] ? 0 : 1;
      columns[column].push(snapshot);
      heights[column] += tileHeight(snapshot) + GRID_GAP_PX;
    }
  }
  if (columns.some((column) => column.length)) groups.push(columns);
  return groups;
}

export function EquipmentGrid({ snapshots }: { snapshots: EquipmentSnapshot[] }) {
  if (snapshots.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-ink-subtle dark:border-slate-700 dark:text-slate-300">
        No equipment registered.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {tileStacks(snapshots).map((columns) => (
        <div
          key={columns[0][0].id}
          className={`grid grid-cols-1 items-start gap-3 ${columns.length === 2 ? "lg:grid-cols-2" : ""}`}
        >
          {columns.map((column, columnIndex) => (
            <div key={columnIndex} className="flex min-w-0 flex-col gap-3">
              {column.map((snapshot) => (
                <div
                  key={snapshot.id}
                  className="grid min-w-0"
                  style={snapshot.kind === "camera"
                    ? { height: tileHeight(snapshot) }
                    : { minHeight: tileHeight(snapshot) }}
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
              ))}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
