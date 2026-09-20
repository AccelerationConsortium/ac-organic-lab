"use client";

import { useState } from "react";

import type { EquipmentSnapshot } from "@/types/api";
import { kindLabel } from "@/lib/format";
import { devicePanelPath } from "@/lib/device-panels";
import { AuthGatedLink } from "./AuthGatedLink";
import { postGenericStartup } from "@/lib/api";
import { useActionError } from "@/lib/use-action-error";
import { useControlLock } from "@/lib/use-control-lock";
import { isMonitoringOnly, kindHasDestructiveControls } from "@/lib/tile-policy";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { MetricList } from "./MetricList";
import { ComponentList } from "./ComponentList";
import { FetchErrorBand } from "./FetchErrorBand";
import { TileShell } from "./TileShell";
import { CameraPlayer } from "./CameraPlayer";

function EmbeddedCamera({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const [shown, setShown] = useState(false);
  const lens = snapshot.camera?.lenses?.[0];
  if (!lens) return null;

  const stream = `${snapshot.id}_${lens.id}`;
  const source = `/streams/api/ws?src=${encodeURIComponent(stream)}`;
  const observed = snapshot.status.components?.["camera"]?.state ?? "unknown";

  return (
    <section className="overflow-hidden rounded-md border border-slate-200 dark:border-slate-700">
      <div className="flex h-9 items-center gap-2 bg-slate-50 px-2 dark:bg-slate-800/40">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-subtle dark:text-slate-400">
          {lens.label} camera
        </span>
        <span className="text-[10px] text-ink-subtle dark:text-slate-500">{observed}</span>
        <button
          type="button"
          onClick={() => setShown((value) => !value)}
          aria-pressed={shown}
          className="ml-auto rounded border border-slate-300 bg-white px-2 py-1 text-xs font-semibold text-ink transition-colors hover:bg-slate-100 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700"
        >
          {shown ? "Hide stream" : "Show stream"}
        </button>
      </div>
      {shown ? (
        <CameraPlayer
          src={source}
          transport={snapshot.camera?.transport}
          className="aspect-video w-full bg-black object-contain"
        />
      ) : (
        <div className="flex aspect-video items-center justify-center bg-slate-950 text-xs text-slate-300">
          Camera view off
        </div>
      )}
    </section>
  );
}

export function EquipmentStatusCard({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const { status } = snapshot;
  const panelPath = devicePanelPath(snapshot.id);
  const metrics = status.metrics ?? {};
  const components = status.components ?? {};
  const hasMetrics = Object.keys(metrics).length > 0;
  const hasComponents = Object.keys(components).length > 0;

  // The lock chip appears on every kind that will eventually expose
  // destructive controls, even before kind-specific buttons are wired
  // up. Once they are, they should respect `locked` from this hook.
  // See lib/tile-policy.ts for the policy and EQUIP_GUIDE.md
  // §6b for the operator-facing explanation. A monitoring-only envelope
  // (a read-only observer of a robot arm, say) has nothing to gate.
  const showsLock = kindHasDestructiveControls(snapshot.kind) && !isMonitoringOnly(snapshot);
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
  const offersInit = !isMonitoringOnly(snapshot) && requiresInit && advertisesStartup && !snapshot.fetch_error;

  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      bannerExtra={panelPath ? (
        // New tab, like the OT-2 tiles: every panel path is a device-served
        // page at the edge, not a route in this app, so a client-side
        // transition would resolve it against the route manifest and 404.
        <AuthGatedLink
          href={panelPath}
          equipmentId={snapshot.id}
          external
          className="inline-flex h-7 items-center gap-1 rounded-md bg-orange-600 px-2.5 text-xs font-semibold text-white transition-colors hover:bg-orange-500"
          title="Open the device's operator panel"
        >
          Control interface ↗
        </AuthGatedLink>
      ) : undefined}
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
      {snapshot.camera && <EmbeddedCamera snapshot={snapshot} />}
      {hasComponents && <ComponentList components={components} />}

      {snapshot.fetch_error && <FetchErrorBand error={snapshot.fetch_error} />}
    </TileShell>
  );
}
