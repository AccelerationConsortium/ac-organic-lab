"use client";

import type { EquipmentSnapshot } from "@/types/api";
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

/** Compact observation-only UR arm status, without physical controls. */
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
