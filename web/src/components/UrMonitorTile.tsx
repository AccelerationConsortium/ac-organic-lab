"use client";

import type { EquipmentSnapshot } from "@/types/api";
import { AuthGatedLink } from "./AuthGatedLink";
import { FetchErrorBand } from "./FetchErrorBand";
import { StatusPill } from "./StatusPill";
import { TileShell } from "./TileShell";

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function Reading({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex h-8 min-w-0 items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-2 dark:border-slate-700 dark:bg-slate-800/40" title={`${label}: ${value ?? "Unknown"}`}>
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">{label}</span>
      <span className="ml-auto truncate text-xs font-semibold text-ink dark:text-slate-100">{value ?? "Unknown"}</span>
    </div>
  );
}

/** Observation-only UR arms. Only the Ligand prototype has a separate workspace. */
export function UrMonitorTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const { status, fetch_error: fetchError } = snapshot;
  const details = fetchError ? {} : status.details ?? {};
  const components = fetchError ? {} : status.components ?? {};
  const mode = text(details.robotmode) ?? text(components.controller?.state);
  const safety = text(details.safetystatus) ?? text(components.safety?.state);
  // Only show program state, not paths or scientific identifiers in the name.
  const program = (text(details.program_state) ?? text(components.program?.state))?.split(/\s+/, 1)[0] ?? null;
  return (
    <TileShell snapshot={snapshot}
      headerRight={<StatusPill state={fetchError ? "unknown" : status.equipment_status} />}
      bannerExtra={<>
        <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-subtle dark:text-slate-400">Read-only monitoring</span>
        {snapshot.id === "ligand_ur5e" && (
          <AuthGatedLink href="/utils/robot_motion" equipmentId={snapshot.id} external
            className="inline-flex h-7 shrink-0 items-center justify-center gap-1 rounded-md border border-orange-300 bg-orange-50 px-2.5 text-xs font-semibold text-orange-800 transition-colors hover:bg-orange-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orange-500 dark:border-orange-700 dark:bg-orange-950/40 dark:text-orange-200 dark:hover:bg-orange-900/60">
            Open control panel ↗
          </AuthGatedLink>
        )}
      </>}
      footerLeft={fetchError ? "Monitor unreachable; current arm state is unknown." : undefined}
    >
      <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-3">
        <Reading label="Mode" value={mode} />
        <Reading label="Safety" value={safety} />
        <Reading label="Program" value={program} />
      </div>
      <p className="text-[11px] text-ink-subtle dark:text-slate-400">Program playing does not necessarily mean the arm is moving.</p>
      {fetchError && <FetchErrorBand error={fetchError} />}
    </TileShell>
  );
}
