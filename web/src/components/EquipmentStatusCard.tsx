"use client";

import type { EquipmentSnapshot } from "@/types/api";
import { kindLabel } from "@/lib/format";
import { useControlLock } from "@/lib/use-control-lock";
import { kindHasDestructiveControls } from "@/lib/tile-policy";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { MetricList } from "./MetricList";
import { ComponentList } from "./ComponentList";
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
  // See lib/tile-policy.ts for the policy and EQUIPMENT_INTEGRATION
  // §6b for the operator-facing explanation.
  const showsLock = kindHasDestructiveControls(snapshot.kind);
  const { locked, countdown, toggle } = useControlLock();

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

      {snapshot.fetch_error && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
          <div className="font-medium">Aggregator could not reach device</div>
          <div className="font-mono">
            {snapshot.fetch_error.kind}
            {snapshot.fetch_error.http_status
              ? ` · HTTP ${snapshot.fetch_error.http_status}`
              : ""}
          </div>
        </div>
      )}
    </TileShell>
  );
}
