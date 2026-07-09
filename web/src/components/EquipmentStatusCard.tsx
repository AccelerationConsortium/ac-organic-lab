"use client";

import type { EquipmentSnapshot } from "@/types/api";
import { kindLabel } from "@/lib/format";
import { useControlLock } from "@/lib/use-control-lock";
import { kindHasDestructiveControls } from "@/lib/tile-policy";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { MetricList } from "./MetricList";
import { ComponentList } from "./ComponentList";
import { FetchErrorBand } from "./FetchErrorBand";
import { TileShell } from "./TileShell";

export function EquipmentStatusCard({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const { status } = snapshot;
  const metrics = status.metrics ?? {};
  const components = status.components ?? {};
  const hasMetrics = Object.keys(metrics).length > 0;
  const hasComponents = Object.keys(components).length > 0;

  // The lock chip appears on every kind that will eventually expose
  // destructive controls, even before kind-specific buttons are wired
  // up. Once they are, they should respect `locked` from this hook.
  // See lib/tile-policy.ts for the policy and EQUIP_GUIDE.md
  // §6b for the operator-facing explanation.
  const showsLock = kindHasDestructiveControls(snapshot.kind);
  const { locked, countdown, toggle } = useControlLock(snapshot.id);

  return (
    <TileShell
      snapshot={snapshot}
      headerRight={
        <>
          {showsLock && (
            <LockButton
              locked={locked}
              countdown={countdown}
              onToggle={toggle}
              noun={kindLabel(snapshot.kind).toLowerCase()}
            />
          )}
          <StatusPill state={status.equipment_status} />
        </>
      }
    >
      {hasMetrics && <MetricList metrics={metrics} />}
      {hasComponents && <ComponentList components={components} />}

      {snapshot.fetch_error && <FetchErrorBand error={snapshot.fetch_error} />}
    </TileShell>
  );
}
