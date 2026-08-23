"use client";

import { useEffect, useState } from "react";

import { AccountsActivitiesTile } from "@/components/AccountsActivitiesTile";
import { LabMap } from "@/components/LabMap";
import { PlatformCard } from "@/components/PlatformCard";
import { useEquipmentList } from "@/lib/use-equipment";
import { HEALTH_DOT, pillClass, platformHealth, stickyPillRow } from "@/lib/pill";
import { usePlatforms } from "@/lib/use-platforms";
import type { EquipmentSnapshot, PlatformSection } from "@/types/api";

const HIDDEN_SECTIONS_KEY = "overview-hidden-sections";

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
        <p className="text-xs text-ink-subtle dark:text-slate-300">
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
        <div className="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-ink-subtle dark:border-slate-700 dark:text-slate-400">
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

  // Section-visibility toggles (same All / None + per-section pill pattern as
  // the Platforms tab). Restored from sessionStorage after mount — not in the
  // initializer, so the SSR HTML matches the first client render.
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  useEffect(() => {
    const saved = window.sessionStorage.getItem(HIDDEN_SECTIONS_KEY);
    if (saved) {
      try {
        setHidden(new Set(JSON.parse(saved) as string[]));
      } catch {
        /* ignore malformed persisted state */
      }
    }
  }, []);
  function persistHidden(next: Set<string>) {
    setHidden(next);
    window.sessionStorage.setItem(HIDDEN_SECTIONS_KEY, JSON.stringify(Array.from(next)));
  }

  // The page layout comes from the static platforms config, which loads fast.
  // We gate the whole page only on that — the equipment list (a per-device
  // status fan-out) fills in the tiles afterward, so the dashboard chrome and
  // section frames paint immediately instead of behind one all-or-nothing
  // "Loading…" gate. See docs/ARCHITECTURE.md: the aggregator now serves a
  // warm cache, so in steady state this second arrival is near-instant.
  if (platformsPending) {
    return <p className="text-sm text-ink-muted dark:text-slate-300">Loading…</p>;
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
  const snapshotsFor = (section: PlatformSection) =>
    section.equipment
      .map((id) => snapshotById.get(id))
      .filter((s): s is EquipmentSnapshot => s !== undefined);

  const sections = platforms.sections;
  const allSectionIds = sections.map((s) => s.id);
  const visibleSections = sections.filter((s) => !hidden.has(s.id));

  return (
    <div className="flex flex-col gap-4">
      {equipmentError && (
        <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
          Failed to load equipment status: {equipmentError.message}
        </p>
      )}

      {/* Section-visibility pills (same format as the Platforms tab): All /
          None shortcuts, then one toggle pill per section with a health dot
          and equipment count. None sits right after All. */}
      <div
        className={stickyPillRow}
        role="group"
        aria-label="Toggle overview sections"
      >
        <button
          type="button"
          onClick={() => persistHidden(new Set())}
          className={pillClass(hidden.size === 0)}
          title="Show every section"
        >
          All
        </button>
        <button
          type="button"
          onClick={() => persistHidden(new Set(allSectionIds))}
          className={pillClass(hidden.size >= allSectionIds.length)}
          title="Hide every section"
        >
          None
        </button>
        {sections.map((section) => {
          const visible = !hidden.has(section.id);
          const health = equipmentReady ? platformHealth(snapshotsFor(section)) : "none";
          return (
            <button
              key={section.id}
              type="button"
              aria-pressed={visible}
              onClick={() => {
                const next = new Set(hidden);
                if (next.has(section.id)) next.delete(section.id);
                else next.add(section.id);
                persistHidden(next);
              }}
              className={pillClass(visible)}
              title={visible ? "Click to hide this section" : "Click to show this section"}
            >
              <span
                className={`inline-block h-2 w-2 rounded-full ${HEALTH_DOT[health]} ${
                  !equipmentReady ? "animate-pulse" : ""
                }`}
                aria-hidden
              />
              {section.title}
              <span className={visible ? "text-sky-600 dark:text-sky-300" : "text-ink-subtle dark:text-slate-400"}>
                {section.equipment.length}
              </span>
            </button>
          );
        })}
      </div>
      {/* Admin headline (renders only for an admin session): the same
          "Accounts & Activities" KPI tile that leads the admin console, laid
          out as one wide row with a GO → link into /admin. */}
      <AccountsActivitiesTile wide adminLink />

      {/* CSS multi-column masonry: every card sits at its own content height
          and packs tightly into the columns (no stretching to match a taller
          neighbour, no gaps below a short one). `break-inside-avoid` keeps a
          card from splitting across the column boundary; `mb-4` is the vertical
          gap between stacked cards (multicol uses margins, not `gap`). */}
      <div className="columns-1 gap-4 lg:columns-2">
        {visibleSections.map((section) => {
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
