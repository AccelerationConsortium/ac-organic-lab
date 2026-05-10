"use client";

import { EquipmentGrid } from "@/components/EquipmentGrid";
import { useEquipmentList } from "@/lib/use-equipment";

export default function HtePlatformPage() {
  const { data, error, isPending } = useEquipmentList();

  if (isPending) {
    return <p className="text-sm text-ink-muted dark:text-slate-400">Loading…</p>;
  }
  if (error) {
    return (
      <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
        Failed to load equipment: {error.message}
      </p>
    );
  }
  if (!data) return null;

  const snapshots = data.equipment.filter((s) => s.platform === "hte");

  return (
    <div className="flex flex-col gap-4">
      <EquipmentGrid snapshots={snapshots} />
    </div>
  );
}
