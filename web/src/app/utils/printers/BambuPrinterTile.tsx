"use client";

import type { BambuAmsTray, EquipmentSnapshot } from "@/types/api";
import { StatusPill } from "@/components/StatusPill";

type Tray = BambuAmsTray;

function unitName(id: number) {
  if (id >= 128 && id < 154) return `HT ${String.fromCharCode(97 + id - 128)}`;
  return id >= 0 && id < 26 ? `AMS ${String.fromCharCode(65 + id)}` : `AMS ${id}`;
}

function color(tray: Tray) {
  const hex = (tray.tray_color ?? "").replace(/^#/, "").toUpperCase();
  const rgb = /^[0-9A-F]{6}(FF)?$/.test(hex) ? `#${hex.slice(0, 6)}` : undefined;
  const names: Record<string, string> = {"#FFFFFF": tray.tray_type === "PLA" ? "Jade White" : "White", "#0086D6": "Cyan", "#A6A9AA": "Silver", "#F72323": "Red", "#000000": "Black"};
  return {rgb, name: tray.tray_color_name ?? (rgb ? names[rgb] ?? "Custom color" : "Unknown color")};
}

export function BambuPrinterTile({ snapshot, onSelect }: { snapshot: EquipmentSnapshot; onSelect: () => void }) {
  const { status } = snapshot;
  const details = status.details ?? {};
  const available = !snapshot.fetch_error && status.equipment_status !== "unknown";
  const running = status.activity === "running";
  const metric = (key: string) => {
    const value = status.metrics?.[key]?.value;
    return available && typeof value === "number" && Number.isFinite(value) ? value : null;
  };
  const progress = metric("print_progress");
  const remaining = metric("remaining_time");
  const trays = available && Array.isArray(details.ams_trays) ? details.ams_trays as Tray[] : [];
  const units = available && Array.isArray(details.ams_unit_ids) ? details.ams_unit_ids as number[] : [...new Set(trays.map(tray => tray.ams_id))];
  const job = typeof details.job_name === "string" ? details.job_name : null;

  return (
    <article className="flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-900">
      <header className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
        <div>
          <p className="text-xs font-medium uppercase tracking-widest text-sky-700 dark:text-sky-400">3D printer · {String(details.model ?? "Bambu Lab")}</p>
          <h2 className="mt-1 text-lg font-semibold text-ink dark:text-slate-100">{snapshot.name}</h2>
        </div>
        <StatusPill state={snapshot.fetch_error ? "unknown" : status.equipment_status} />
      </header>
      <div className="flex flex-1 flex-col gap-5 p-5">
        <div>
          <div className="flex items-center justify-between gap-3">
            <p className="truncate text-sm font-medium text-ink dark:text-slate-100">{!available ? "Telemetry unavailable" : running ? job || "Print in progress" : "No active print"}</p>
            {available && running && progress !== null && <span className="text-sm font-semibold tabular-nums text-sky-700 dark:text-sky-400">{Math.round(progress)}%</span>}
          </div>
          {available && running && <>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800" role="progressbar" aria-label="Print progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress === null ? undefined : Math.max(0, Math.min(100, progress))}>
              {progress !== null && <div className="h-full rounded-full bg-sky-500" style={{width: `${Math.max(0, Math.min(100, progress))}%`}} />}
            </div>
            <p className="mt-2 text-xs text-ink-subtle dark:text-slate-400">{remaining === null ? "Remaining time unavailable" : `${Math.round(remaining)} min remaining`}{metric("current_layer") !== null ? ` · Layer ${metric("current_layer")}${metric("total_layers") !== null ? ` / ${metric("total_layers")}` : ""}` : ""}</p>
          </>}
          {!available && <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">Live inventory and temperatures are withheld until telemetry recovers.</p>}
        </div>
        <dl className="grid grid-cols-3 gap-2 rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60">
          {[["Nozzle", "nozzle_temperature"], ["Bed", "bed_temperature"], ["Chamber", "chamber_temperature"]].map(([label, key]) => <div key={key}>
            <dt className="text-xs text-ink-subtle dark:text-slate-400">{label}</dt>
            <dd className="mt-1 font-semibold tabular-nums text-ink dark:text-slate-100">{metric(key) === null ? "—" : `${Math.round(metric(key)!)}°C`}</dd>
          </div>)}
        </dl>
        <section className="flex-1" aria-label={`${snapshot.name} filament inventory`}>
          <div className="mb-3 flex items-center justify-between"><h3 className="text-xs font-semibold uppercase tracking-wider text-ink-subtle dark:text-slate-400">Filament inventory</h3><span className="text-xs text-ink-subtle dark:text-slate-400">{available ? `${trays.length} loaded` : "Unavailable"}</span></div>
          {units.length === 0 && <p className="text-sm text-ink-subtle dark:text-slate-400">No AMS inventory reported.</p>}
          <div className="space-y-3">
            {units.map(unit => <div key={unit}>
              <h4 className="mb-1 text-xs font-medium text-ink-muted dark:text-slate-300">{unitName(unit)}</h4>
              {trays.filter(tray => tray.ams_id === unit).length === 0 ? <p className="text-xs text-ink-subtle dark:text-slate-400">Empty · no loaded filament reported</p> :
                <div className="grid gap-1.5 sm:grid-cols-2">{trays.filter(tray => tray.ams_id === unit).map(tray => {
                  const display = color(tray);
                  return <div key={tray.tray_id} className="flex items-center gap-2 rounded-md border border-slate-100 px-2 py-2 dark:border-slate-800" title={tray.tray_color_source === "operator_declared" ? "Color declared by operator" : "Color match from reported telemetry"}>
                    <span aria-hidden="true" className="h-5 w-5 shrink-0 rounded-full border border-slate-300 dark:border-slate-600" style={{background: display.name.toLowerCase() === "transparent" ? "repeating-conic-gradient(#cbd5e1 0% 25%, #fff 0% 50%) 0 / 8px 8px" : display.rgb ?? "transparent"}} />
                    <div className="min-w-0 flex-1"><p className="truncate text-xs font-medium text-ink dark:text-slate-200">{unit < 26 ? `${String.fromCharCode(65 + unit)}${tray.tray_id + 1}` : unitName(unit)} · {tray.tray_type ?? "Unknown"}</p><p className="truncate text-xs text-ink-subtle dark:text-slate-400">{display.name}</p></div>
                    <span className={`text-xs tabular-nums ${tray.remaining_percent != null && tray.remaining_percent < 20 ? "text-amber-700 dark:text-amber-400" : "text-ink-subtle dark:text-slate-400"}`}>{tray.remaining_percent == null ? "—" : `${tray.remaining_percent}%`}</span>
                  </div>;
                })}</div>}
            </div>)}
          </div>
        </section>
        <button type="button" onClick={onSelect} className="w-full rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm font-medium text-sky-800 hover:bg-sky-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-500 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-300 dark:hover:bg-sky-900">Prepare a job · view queue</button>
      </div>
    </article>
  );
}
