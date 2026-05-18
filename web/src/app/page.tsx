"use client";

import { LabMap } from "@/components/LabMap";
import { PlatformCard } from "@/components/PlatformCard";
import { useEquipmentList } from "@/lib/use-equipment";
import { usePlatforms } from "@/lib/use-platforms";
import type { EquipmentSnapshot, PlatformSection } from "@/types/api";

function LabEnvironmentCard({
  section,
  sensors,
}: {
  section: PlatformSection;
  sensors: EquipmentSnapshot[];
}) {
  return (
    <section className="flex flex-col rounded-2xl border border-slate-200 bg-surface-raised p-4 dark:border-slate-800 dark:bg-slate-900">
      <header className="mb-3">
        <h2 className="text-base font-semibold text-ink dark:text-slate-100">
          {section.title}
        </h2>
        <p className="text-xs text-ink-subtle dark:text-slate-400">
          {sensors.length === 0
            ? "No environmental sensors configured."
            : section.description
              ? `${section.description} · ${sensors.length} stations · hover a marker for details.`
              : `${sensors.length} stations · hover a marker for details.`}
        </p>
      </header>
      {sensors.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-ink-subtle dark:border-slate-700 dark:text-slate-500">
          Add entries with
          <span className="mx-1 font-mono">kind: environmental_sensor</span>
          and a <span className="font-mono">location</span> in
          <span className="ml-1 font-mono">equipment.yaml</span>.
        </div>
      ) : (
        <LabMap sensors={sensors} />
      )}
    </section>
  );
}

export default function OverviewPage() {
  const { data: equipmentData, error: equipmentError, isPending: equipmentPending } =
    useEquipmentList();
  const { data: platforms, error: platformsError, isPending: platformsPending } =
    usePlatforms();

  if (equipmentPending || platformsPending) {
    return <p className="text-sm text-ink-muted dark:text-slate-400">Loading…</p>;
  }
  if (equipmentError) {
    return (
      <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
        Failed to load equipment: {equipmentError.message}
      </p>
    );
  }
  if (platformsError) {
    return (
      <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
        Failed to load platforms: {platformsError.message}
      </p>
    );
  }
  if (!equipmentData || !platforms) return null;

  const snapshotById = new Map<string, EquipmentSnapshot>(
    equipmentData.equipment.map((s) => [s.id, s]),
  );

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {platforms.sections.map((section) => {
        if (section.kind === "environmental_map") {
          const sensors = section.equipment
            .map((id) => snapshotById.get(id))
            .filter((s): s is EquipmentSnapshot => s !== undefined && s.location != null);
          return (
            <LabEnvironmentCard key={section.id} section={section} sensors={sensors} />
          );
        }

        // kind === "platform"
        const snapshots = section.equipment
          .map((id) => snapshotById.get(id))
          .filter((s): s is EquipmentSnapshot => s !== undefined);
        return (
          <PlatformCard
            key={section.id}
            id={section.id}
            title={section.title}
            description={section.description ?? undefined}
            href={section.href ?? undefined}
            snapshots={snapshots}
          />
        );
      })}
    </div>
  );
}
