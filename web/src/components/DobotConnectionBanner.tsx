"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useEquipmentStatus } from "@/lib/use-equipment";

export function DobotConnectionBanner() {
  const { data: snapshot, error } = useEquipmentStatus("dobot_mg400");
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, []);

  const timestamp = snapshot ? Date.parse(snapshot.fetched_at) : NaN;
  const stale = !!snapshot && (!Number.isFinite(timestamp) || now - timestamp > 30_000);
  const unavailable = !!error || !!snapshot?.fetch_error || stale;
  const simulated = snapshot?.simulated === true || snapshot?.status.equipment_status === "dry_run";
  const controller = snapshot?.status.components?.controller;
  const connected = !unavailable && !simulated && controller?.connected === true;
  const label = unavailable
    ? stale ? "Connection unknown · status stale" : "Connection unknown · gateway unavailable"
    : simulated
      ? "Simulation · physical connection not verified"
      : connected
        ? `Connected${controller?.state ? ` · ${controller.state}` : ""}`
        : controller?.connected === false
          ? "Disconnected"
          : snapshot ? "Connection unknown" : "Checking connection…";

  return (
    <div className="shrink-0 border-b border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900" role="status">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-2 gap-y-1 px-4 py-1.5 text-xs text-ink-muted dark:text-slate-300 sm:px-6 lg:px-8">
        <span className={`h-2 w-2 shrink-0 rounded-full ${connected ? "bg-emerald-500" : "bg-amber-500"}`} aria-hidden />
        <span className="font-semibold">Dobot MG400</span>
        <span>{label}</span>
        <Link href="/platforms/ligand_development" className="ml-auto underline underline-offset-2">
          Ligand Development
        </Link>
      </div>
    </div>
  );
}
