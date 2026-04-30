"use client";

import { LabMap } from "@/components/LabMap";
import { PlatformCard } from "@/components/PlatformCard";
import { useEquipmentList } from "@/lib/use-equipment";
import type { EquipmentSnapshot } from "@/types/api";

interface PlatformDef {
  id: string;
  title: string;
  description?: string;
  href?: string;
}

const PLATFORMS: PlatformDef[] = [
  {
    id: "hte",
    title: "HTE Platform",
    description: "High-throughput platform for chemistry",
    href: "/platforms/hte",
  },
];

function LabEnvironmentCard({ sensors }: { sensors: EquipmentSnapshot[] }) {
  return (
    <section className="flex flex-col rounded-2xl border border-slate-200 bg-surface-raised p-4 dark:border-slate-800 dark:bg-slate-900">
      <header className="mb-3">
        <h2 className="text-base font-semibold text-ink dark:text-slate-100">
          Lab Environment
        </h2>
        <p className="text-xs text-ink-subtle dark:text-slate-400">
          {sensors.length === 0
            ? "No environmental sensors configured."
            : `Temperature, humidity, O₂, and VOC · ${sensors.length} stations · hover a marker for details.`}
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

export default function LabOverviewPage() {
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

  const all = data.equipment;
  const sensors = all.filter(
    (s: EquipmentSnapshot) => s.kind === "environmental_sensor" && s.location
  );

  // Group non-sensor equipment by platform.
  const byPlatform = new Map<string, EquipmentSnapshot[]>();
  for (const snapshot of all) {
    if (snapshot.kind === "environmental_sensor") continue;
    const arr = byPlatform.get(snapshot.platform) ?? [];
    arr.push(snapshot);
    byPlatform.set(snapshot.platform, arr);
  }

  // Known platforms in display order, then any unknown ones from the registry.
  const knownIds = new Set(PLATFORMS.map((p) => p.id));
  const platformsToShow: PlatformDef[] = [
    ...PLATFORMS,
    ...Array.from(byPlatform.keys())
      .filter((id) => !knownIds.has(id))
      .map((id) => ({ id, title: id })),
  ];

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {/* Lab Environment occupies the top-left cell. */}
      <LabEnvironmentCard sensors={sensors} />

      {/* Platforms fill the remaining cells, starting top-right. */}
      {platformsToShow.map((p) => (
        <PlatformCard
          key={p.id}
          id={p.id}
          title={p.title}
          description={p.description}
          href={p.href}
          snapshots={byPlatform.get(p.id) ?? []}
        />
      ))}
    </div>
  );
}
