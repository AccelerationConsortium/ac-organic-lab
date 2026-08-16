"use client";

import { useEquipmentList } from "@/lib/use-equipment";
import { BambuPrinterPanel } from "./BambuPrinterPanel";
import { HostsPanel } from "./HostsPanel";

const BAMBU_PRINTER_IDS = new Set(["bambu_p1s_01", "bambu_h2d_01"]);

/**
 * Devices utility — the lab's host machines (servers + device PCs, with live
 * host-ops status where the ops agent is deployed) followed by the Bambu
 * printer monitoring panel. Generalises the former /utils/bambu_printer page,
 * which now redirects here.
 */
export default function DevicesPage() {
  const { data, error, isPending } = useEquipmentList();

  if (isPending) {
    return <p className="text-sm text-ink-muted dark:text-slate-400">Loading devices…</p>;
  }
  if (error) {
    return (
      <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
        Failed to load device status: {error.message}
      </p>
    );
  }

  const equipment = data?.equipment ?? [];
  const printers = equipment.filter((snapshot) => BAMBU_PRINTER_IDS.has(snapshot.id));
  return (
    <div className="flex flex-col gap-8">
      <HostsPanel snapshots={equipment} />
      <BambuPrinterPanel printers={printers} />
    </div>
  );
}
