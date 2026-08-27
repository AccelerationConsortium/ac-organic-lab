"use client";

import { useEquipmentList } from "@/lib/use-equipment";
import { HostsPanel } from "./HostsPanel";

/**
 * Computers and Servers — the lab's host machines (servers + device PCs),
 * with live host-ops status where the ops agent is deployed and an SSH
 * terminal link per machine. Split out of the former combined /utils/devices
 * page, whose printer half now lives at /utils/printers.
 */
export default function ComputersPage() {
  const { data, error, isPending } = useEquipmentList();

  if (isPending) {
    return <p className="text-sm text-ink-muted dark:text-slate-300">Loading hosts…</p>;
  }
  if (error) {
    return (
      <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
        Failed to load host status: {error.message}
      </p>
    );
  }

  return <HostsPanel snapshots={data?.equipment ?? []} />;
}
