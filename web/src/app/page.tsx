"use client";

import { LabMap } from "@/components/LabMap";
import { PlatformCard } from "@/components/PlatformCard";
import { useEquipmentList } from "@/lib/use-equipment";
import { usePlatforms } from "@/lib/use-platforms";
import type { EquipmentSnapshot, PlatformSection } from "@/types/api";

function LabEnvironmentCard({
  section,
  sensors,
  pending = false,
}: {
  section: PlatformSection;
  sensors: EquipmentSnapshot[];
  pending?: boolean;
}) {
  return (
    <section className="flex flex-col rounded-2xl border border-slate-200 bg-surface-raised p-4 dark:border-slate-800 dark:bg-slate-900">
      <header className="mb-3">
        <h2 className="text-base font-semibold text-ink dark:text-slate-100">
          {section.title}
        </h2>
        <p className="text-xs text-ink-subtle dark:text-slate-400">
          {pending
            ? "Loading sensors…"
            : sensors.length === 0
              ? "No environmental sensors configured."
              : section.description
                ? `${section.description} · ${sensors.length} stations · hover a marker for details.`
                : `${sensors.length} stations · hover a marker for details.`}
        </p>
      </header>
      {pending ? (
        <div className="aspect-[2/1] w-full animate-pulse rounded-xl bg-slate-200 dark:bg-slate-800" />
      ) : sensors.length === 0 ? (
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

  // The page layout comes from the static platforms config, which loads fast.
  // We gate the whole page only on that — the equipment list (a per-device
  // status fan-out) fills in the tiles afterward, so the dashboard chrome and
  // section frames paint immediately instead of behind one all-or-nothing
  // "Loading…" gate. See docs/ARCHITECTURE.md: the aggregator now serves a
  // warm cache, so in steady state this second arrival is near-instant.
  if (platformsPending) {
    return <p className="text-sm text-ink-muted dark:text-slate-400">Loading…</p>;
  }
  if (platformsError) {
    return (
      <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
        Failed to load platforms: {platformsError.message}
      </p>
    );
  }
  if (!platforms) return null;

  const equipmentReady = !!equipmentData;
  const snapshotById = new Map<string, EquipmentSnapshot>(
    (equipmentData?.equipment ?? []).map((s) => [s.id, s]),
  );

  return (
    <div className="flex flex-col gap-4">
      {equipmentError && (
        <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
          Failed to load equipment status: {equipmentError.message}
        </p>
      )}
      {/* CSS multi-column masonry: every card sits at its own content height
          and packs tightly into the columns (no stretching to match a taller
          neighbour, no gaps below a short one). `break-inside-avoid` keeps a
          card from splitting across the column boundary; `mb-4` is the vertical
          gap between stacked cards (multicol uses margins, not `gap`). */}
      <div className="columns-1 gap-4 lg:columns-2">
        {platforms.sections.map((section) => {
          let card;
          if (section.kind === "environmental_map") {
            const sensors = section.equipment
              .map((id) => snapshotById.get(id))
              .filter((s): s is EquipmentSnapshot => s !== undefined && s.location != null);
            card = (
              <LabEnvironmentCard
                section={section}
                sensors={sensors}
                pending={!equipmentReady}
              />
            );
          } else {
            // kind === "platform"
            const snapshots = section.equipment
              .map((id) => snapshotById.get(id))
              .filter((s): s is EquipmentSnapshot => s !== undefined);
            card = (
              <PlatformCard
                id={section.id}
                title={section.title}
                description={section.description ?? undefined}
                href={section.href ?? undefined}
                snapshots={snapshots}
                pending={!equipmentReady}
                expectedCount={section.equipment.length}
              />
            );
          }
          return (
            <div key={section.id} className="mb-4 break-inside-avoid">
              {card}
            </div>
          );
        })}
      </div>
    </div>
  );
}
