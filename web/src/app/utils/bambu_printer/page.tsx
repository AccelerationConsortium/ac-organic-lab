"use client";

import { useEquipmentList } from "@/lib/use-equipment";
import { BambuPrinterPanel } from "./BambuPrinterPanel";

const BAMBU_PRINTER_IDS = new Set(["bambu_p1s_01", "bambu_h2d_01"]);

export default function BambuPrinterPage() {
  const { data, error, isPending } = useEquipmentList();

  if (isPending) {
    return <p className="text-sm text-ink-muted dark:text-slate-400">Loading printers…</p>;
  }
  if (error) {
    return (
      <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
        Failed to load Bambu printer status: {error.message}
      </p>
    );
  }

  const printers = (data?.equipment ?? []).filter((snapshot) =>
    BAMBU_PRINTER_IDS.has(snapshot.id),
  );
  return <BambuPrinterPanel printers={printers} />;
}
