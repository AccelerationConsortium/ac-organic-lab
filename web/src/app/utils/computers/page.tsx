"use client";

import { useEquipmentList } from "@/lib/use-equipment";
import { useLabHosts } from "@/lib/use-hosts";
import { HostsPanel } from "./HostsPanel";

/**
 * Computers and Servers — the lab's host machines (servers + device PCs).
 * The inventory itself comes from `GET /api/hosts` (services, ports and
 * domains derived from equipment.yaml); the equipment list supplies the live
 * status per service, including the host-ops agents' whitelists. Split out of
 * the former combined /utils/devices page, whose printer half now lives at
 * /utils/printers.
 */
export default function ComputersPage() {
  const hosts = useLabHosts();
  const equipment = useEquipmentList();

  if (hosts.isPending || equipment.isPending) {
    return <p className="text-sm text-ink-muted dark:text-slate-300">Loading hosts…</p>;
  }
  const error = hosts.error ?? equipment.error;
  if (error || !hosts.data) {
    return (
      <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
        Failed to load host inventory: {error?.message ?? "no data"}
      </p>
    );
  }

  return <HostsPanel hosts={hosts.data} snapshots={equipment.data?.equipment ?? []} />;
}
