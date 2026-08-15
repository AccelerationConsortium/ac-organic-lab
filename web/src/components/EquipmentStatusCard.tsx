"use client";

import type { EquipmentSnapshot } from "@/types/api";
import { kindLabel } from "@/lib/format";
import { postGenericStartup } from "@/lib/api";
import { useActionError } from "@/lib/use-action-error";
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
  const { actionError, exec, isPending } = useActionError();

  // Generic INIT: a `requires_init` device that itself advertises the
  // standard `startup` verb gets the template's initialize button even
  // without a kind-specific tile — a service restart must never strand a
  // device in requires_init with no dashboard recovery (the doser after
  // the 2026-07-31 reboot; the fume-hood Pi after the 2026-08-14 outage).
  // The device is the authority: no advertised `startup`, no button. Only
  // this direction is wired — shutdown stays with kind-specific tiles.
  const requiresInit = status.equipment_status === "requires_init";
  const advertisesStartup =
    (status.allowed_actions ?? []).includes("startup") ||
    (status.required_actions ?? []).includes("startup");
  const offersInit = requiresInit && advertisesStartup && !snapshot.fetch_error;

  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      lifecycle={
        offersInit
          ? {
              isOn: false,
              initLabel: "INIT",
              onPowerToggle: () => exec(() => postGenericStartup(snapshot.id)),
              disabled: locked || isPending,
              powerTitle: "Device needs initialization — click to run startup",
            }
          : undefined
      }
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
