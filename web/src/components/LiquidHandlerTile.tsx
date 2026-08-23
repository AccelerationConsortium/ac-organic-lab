"use client";

import { useQuery } from "@tanstack/react-query";

import { getDeckLayout } from "@/lib/api";
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

function parseLights(snapshot: EquipmentSnapshot): LightsState {
  const raw = snapshot.status.components?.["lights"]?.state;
  if (raw === "on" || raw === "off") return raw;
  return "unknown";
}

// Rendered on the tile as their own pills, so they are excluded from the
// generic ComponentList below to avoid duplication.
const TILE_OWNED_COMPONENTS = new Set([
  "lights",
  "pipette_left",
  "pipette_right",
  "ssh",
  "protocol",
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
  const metrics = status.metrics ?? {};
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

  // Deck source: the device's own normalized deck; legacy dashboard-store
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
  const robotModules = robotModulesFromStatus(status);

  return (
    <TileShell
      snapshot={snapshot}
      headerRight={<StatusPill state={status.equipment_status} />}
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
            {/* Read-only lights indicator (the toggle lives in the device panel). */}
            <span
              className="flex h-7 items-center rounded-md border border-slate-200 bg-white px-2 text-xs font-semibold text-ink dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-100"
              title={`Deck lights: ${lights}`}
            >
              <span
                className={[
                  "mr-1.5 inline-block h-2.5 w-2.5 rounded-full",
                  lights === "on"
                    ? "bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.7)]"
                    : lights === "off"
                      ? "bg-slate-900 dark:bg-black"
                      : "bg-slate-400 dark:bg-slate-500",
                ].join(" ")}
                aria-hidden
              />
              Light
            </span>
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

      {/* SSH / Protocol status pills. */}
      <div className="flex flex-wrap items-center justify-end gap-1.5">
        {(["ssh", "protocol"] as const).map((key) => {
          const c = components[key];
          if (!c) return null;
          const ok = c.state === "connected" || c.state === "ready";
          return (
            <span
              key={key}
              className="flex h-7 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2 dark:border-slate-700 dark:bg-slate-800/60"
              title={`${key === "ssh" ? "SSH" : "Protocol"}: ${c.state}`}
            >
              <span
                className={`inline-block h-2 w-2 rounded-full ${
                  ok ? "bg-emerald-400" : "bg-slate-400 dark:bg-slate-500"
                }`}
                aria-hidden
              />
              <span className="text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
                {key === "ssh" ? "SSH" : "Protocol"}
              </span>
            </span>
          );
        })}
      </div>

      {Object.keys(metrics).length > 0 && <MetricList metrics={metrics} />}
      {Object.keys(otherComponents).length > 0 && (
        <ComponentList components={otherComponents} />
      )}

      {snapshot.fetch_error && <FetchErrorBand error={snapshot.fetch_error} />}
    </TileShell>
  );
}
