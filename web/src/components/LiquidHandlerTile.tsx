"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getDeckLayout, postOt2Lights } from "@/lib/api";
import { useActionError } from "@/lib/use-action-error";
import { useUserAuth } from "@/lib/user-auth";
import { devicePanelPath } from "@/lib/device-panels";
import type { EquipmentSnapshot } from "@/types/api";
import {
  deviceDeckFromStatus,
  pipetteLabel,
  robotModulesFromStatus,
  tipRacksFromStatus,
} from "@/lib/ot2-deck";

import { AuthGatedLink } from "./AuthGatedLink";
import { ComponentList } from "./ComponentList";
import { DeckPanel } from "./DeckPanel";
import { FetchErrorBand } from "./FetchErrorBand";
import { MetricList } from "./MetricList";
import { StatusPill } from "./StatusPill";
import { TileShell } from "./TileShell";

type LightsState = "on" | "off" | "unknown";

const LIGHT_DOT: Record<LightsState, string> = {
  on: "bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.7)]",
  off: "bg-slate-900 dark:bg-black",
  unknown: "bg-slate-400 dark:bg-slate-500",
};

function LightsPill({
  state,
  interactive,
  onToggle,
}: {
  state: LightsState;
  interactive: boolean;
  onToggle: () => void;
}) {
  const base =
    "flex h-7 items-center rounded-md border border-slate-200 bg-white px-2 text-xs font-semibold text-ink dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-100";
  const dot = (
    <span className={`mr-1.5 inline-block h-2.5 w-2.5 rounded-full ${LIGHT_DOT[state]}`} aria-hidden />
  );
  if (!interactive) {
    return (
      <span className={base} title={`Deck lights: ${state} (sign in with a role on this robot to switch them)`}>
        {dot}
        Light
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`${base} transition-colors hover:bg-slate-50 dark:hover:bg-slate-700/60`}
      title={`Deck lights: ${state} — click to turn ${state === "on" ? "off" : "on"}`}
      aria-pressed={state === "on"}
    >
      {dot}
      Light
    </button>
  );
}

function parseLights(snapshot: EquipmentSnapshot): LightsState {
  const raw = snapshot.status.components?.["lights"]?.state;
  if (raw === "on" || raw === "off") return raw;
  return "unknown";
}

// Already represented by the tile's status, pipette pills, and footer details.
const TILE_OWNED_COMPONENTS = new Set([
  "lights",
  "pipette_left",
  "pipette_right",
  "ssh",
  "protocol",
  "control",
  "robot",
]);

/**
 * Read-only OT-2 tile. All control (session lifecycle, lights, declaring
 * deck intent) lives in the gateway's own operator panel, which the tile
 * links to directly at its edge path (`/ot2/<instance>/ui/`) rather than
 * through a dashboard page that framed it — one interface, not two. See
 * docs/UI_DESIGN.md §1.
 */
export function LiquidHandlerTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const { status } = snapshot;
  const metrics = Object.fromEntries(
    Object.entries(status.metrics ?? {}).filter(([key]) => key !== "cycles_total"),
  );
  const components = status.components ?? {};
  const otherComponents = Object.fromEntries(
    Object.entries(components).filter(([k]) => !TILE_OWNED_COMPONENTS.has(k)),
  );
  const pipLeft = components["pipette_left"];
  const pipRight = components["pipette_right"];
  const lights = parseLights(snapshot);
  // Edge path of the gateway's own panel; null for a liquid handler that
  // hosts none, in which case the tile simply offers no link.
  const panelPath = devicePanelPath(snapshot.id);

    // The gateway answers HTTP 200 even while its robot is off the
    // network — it reports the robot's reachability explicitly, and we surface
    // that on the tile so a "Needs init" (amber) is never mistaken for a
    // healthy boot. (Absent flag = treat as reachable; only an explicit
    // False trips the offline pill.)
    const robotReachable =
      (status.details?.robot as Record<string, unknown> | undefined)
        ?.reachable as boolean | undefined;

    // Deck source:the device's own normalized deck; legacy dashboard-store
  // fallback (read-only here) for gateways that don't publish it yet.
  const deviceDeck = deviceDeckFromStatus(status);
  const migrated = deviceDeck != null;
  const { data: legacyDeck } = useQuery({
    queryKey: ["deck", snapshot.id] as const,
    queryFn: () => getDeckLayout(snapshot.id),
    refetchInterval: 15000,
    enabled: !migrated,
  });
  const legacyLabware = legacyDeck?.slots ?? {};

  // Deck light. Convenience-class, so no lock chip and no auto-relock
  // countdown — those guard destructive controls. It still needs a role on
  // this robot, because the passthrough authorizes every action alike.
  const queryClient = useQueryClient();
  const { authenticated, canControl, requestLogin } = useUserAuth();
  const mayToggleLights = authenticated && canControl(snapshot.id);
  const { actionError, exec } = useActionError();
  const [pendingLights, setPendingLights] = useState<boolean | null>(null);
  // Drop the optimistic value once the 2.5 s poll reports the device agreeing,
  // so a refused or externally-reverted toggle cannot leave the pill lying.
  useEffect(() => {
    if (pendingLights != null && lights === (pendingLights ? "on" : "off")) {
      setPendingLights(null);
    }
  }, [lights, pendingLights]);
  const shownLights: LightsState =
    pendingLights == null ? lights : pendingLights ? "on" : "off";

  function toggleLights() {
    if (!authenticated) {
      requestLogin();
      return;
    }
    if (!mayToggleLights) return;
    const next = shownLights !== "on";
    setPendingLights(next);
    exec(() => postOt2Lights(snapshot.id, next).then(() => queryClient.invalidateQueries({ queryKey: ["equipment"] })), {
      action: "lights.set",
      onError: () => setPendingLights(null),
    });
  }
  const robotModules = robotModulesFromStatus(status);

  // The gateway can answer while its robot is offline; an explicit
  // robot.reachable=false trips a red "Robot offline" pill next to the status pill
  // so the tile never reads merely "Needs init" when the robot is actually gone.

  const robotOffline = robotReachable === false;
  const control = components.control;
  const httpState = !control ? "unknown"
    : control.state === "http" && control.connected ? "connected"
    : "disconnected";
  const connectionDetails = [
    components.ssh && `SSH: ${components.ssh.state}`,
    `HTTP: ${httpState}`,
    components.protocol && `Protocol: ${components.protocol.state}`,
  ].filter(Boolean).join(" · ");


  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      footerMessageTitle={[status.message, connectionDetails].filter(Boolean).join(" · ")}
      headerRight={
        <>
          {robotOffline && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-100 px-2.5 py-0.5 text-xs font-medium text-rose-900 ring-1 ring-inset ring-rose-300 dark:bg-rose-900/30 dark:text-rose-200 dark:ring-rose-800">
              <span className="h-1.5 w-1.5 rounded-full bg-current opacity-70" />
              Robot offline
            </span>
          )}
          {!robotOffline && <StatusPill state={status.equipment_status} />}
        </>
      }
      bannerExtra={
        <>
          {panelPath && (
            <AuthGatedLink
              href={panelPath}
              equipmentId={snapshot.id}
              external
              className="inline-flex h-7 items-center gap-1 rounded-md bg-orange-600 px-2.5 text-xs font-semibold text-white transition-colors hover:bg-orange-500 dark:bg-orange-600 dark:hover:bg-orange-500"
              title="Open the OT-2 gateway's operator panel (deck declaration, session lifecycle, lights, tips, claim)"
            >
              Control interface ↗
            </AuthGatedLink>
          )}
          <div className="ml-auto flex items-center gap-1.5">
            {/* Deck light. A plain indicator without a role on this robot;
                a toggle with one. */}
            <LightsPill
              state={shownLights}
              interactive={mayToggleLights}
              onToggle={toggleLights}
            />
            <span
              className="flex h-7 items-center rounded-md border border-slate-200 bg-white px-2 text-xs font-semibold text-ink dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-100"
              title={`Left mount: ${pipLeft?.state ?? "empty"}`}
            >
              {pipetteLabel(pipLeft?.state)}
            </span>
            <span
              className="flex h-7 items-center rounded-md border border-slate-200 bg-white px-2 text-xs font-semibold text-ink dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-100"
              title={`Right mount: ${pipRight?.state ?? "empty"}`}
            >
              {pipetteLabel(pipRight?.state)}
            </span>
          </div>
        </>
      }
    >
      {/* Deck — 12 slots (1 bottom-left … 12 top-right), read-only mirror of
          the device's /status deck (observed + declared, mismatch flagged).
          Editing happens on the control page. */}
      <DeckPanel
        deviceDeck={deviceDeck}
        legacyLabware={legacyLabware}
        robotModules={robotModules}
        variant="tile"
        tipRacks={tipRacksFromStatus(status)}
      />

      {Object.keys(metrics).length > 0 && <MetricList metrics={metrics} />}
      {Object.keys(otherComponents).length > 0 && (
        <ComponentList components={otherComponents} />
      )}

      {snapshot.fetch_error && <FetchErrorBand error={snapshot.fetch_error} />}
    </TileShell>
  );
}
