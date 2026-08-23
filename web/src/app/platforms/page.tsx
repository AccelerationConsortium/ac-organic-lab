"use client";

import { useEffect, useState } from "react";

import { EquipmentGrid } from "@/components/EquipmentGrid";
import { useEquipmentList } from "@/lib/use-equipment";
import { HEALTH_DOT, equipmentPillClass, pillClass, platformHealth, stickyPillRowBase } from "@/lib/pill";
import { usePlatforms } from "@/lib/use-platforms";
import type { EquipmentSnapshot, PlatformSection } from "@/types/api";

/**
 * The Platforms tab: every platform section with a detail page as a pill
 * (plus None, which shows nothing). Under the platform pills, a collapsed
 * "Equipment filters" disclosure expands to per-equipment pills that toggle
 * individual tiles on/off (All / None shortcuts). The selected platform's
 * equipment renders in the same full-width EquipmentGrid the per-platform
 * pages use.
 */

const SELECTED_PLATFORM_KEY = "platforms-selected";
const EQUIPMENT_OPEN_KEY = "platforms-equipment-open";
const NONE = "__none__";

export default function PlatformsPage() {
  const { data: equipmentData, error: equipmentError } = useEquipmentList();
  const { data: platforms, error: platformsError, isPending: platformsPending } =
    usePlatforms();

  // Selected pill (a section id, or NONE). Restored from sessionStorage after
  // mount (not in the initializer — SSR HTML must match the first render).
  const [selectedId, setSelectedId] = useState<string | null>(null);
  useEffect(() => {
    const saved = window.sessionStorage.getItem(SELECTED_PLATFORM_KEY);
    if (saved) setSelectedId(saved);
  }, []);

  // Per-platform hidden-equipment sets (equipment pills toggle visibility).
  const [hiddenByPlatform, setHiddenByPlatform] = useState<Record<string, string[]>>({});

  // Equipment-pill row is collapsed/auto-hidden by default so only the
  // platform pills stay visible; expand it to toggle individual tiles.
  // Restored from/saved to sessionStorage like the selected platform.
  const [equipmentOpen, setEquipmentOpen] = useState(false);
  useEffect(() => {
    const saved = window.sessionStorage.getItem(EQUIPMENT_OPEN_KEY);
    if (saved != null) setEquipmentOpen(saved === "1");
  }, []);

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
  const snapshotsFor = (section: PlatformSection) =>
    section.equipment
      .map((id) => snapshotById.get(id))
      .filter((s): s is EquipmentSnapshot => s !== undefined);

  // Only real bench platforms (sections with a detail page) get a pill —
  // href-less sections like Services stay on the Overview's summary cards.
  const platformSections = platforms.sections.filter(
    (s) => s.kind === "platform" && s.href != null,
  );

  const showNone = selectedId === NONE;
  const selected = showNone
    ? null
    : platformSections.find((s) => s.id === selectedId) ?? platformSections[0] ?? null;

  function selectPlatform(id: string) {
    setSelectedId(id);
    window.sessionStorage.setItem(SELECTED_PLATFORM_KEY, id);
  }

  const hidden = selected ? new Set(hiddenByPlatform[selected.id] ?? []) : new Set<string>();
  function setHidden(next: Set<string>) {
    if (!selected) return;
    setHiddenByPlatform((m) => ({ ...m, [selected.id]: Array.from(next) }));
  }
  function toggleEquipment(id: string) {
    const next = new Set(hidden);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setHidden(next);
  }

  const visibleSnapshots = selected
    ? snapshotsFor(selected).filter((s) => !hidden.has(s.id))
    : [];

  return (
    <div className="flex flex-col gap-4">
      {equipmentError && (
        <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
          Failed to load equipment status: {equipmentError.message}
        </p>
      )}

      {/* Both pill rows pin as ONE block. Stacking two independently-sticky
          rows would need the second's `top` to equal the first's rendered
          height — unknowable here (the row wraps to 2 lines on a narrow
          screen), and getting it wrong makes them overlap and judder. One
          container has no such offset to get wrong. */}
      <div className={`${stickyPillRowBase} flex flex-col gap-2`}>
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Platforms">
          {platformSections.map((section) => {
            const active = selected?.id === section.id;
            const health = equipmentReady ? platformHealth(snapshotsFor(section)) : "none";
            return (
              <button
                key={section.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => selectPlatform(section.id)}
                className={pillClass(active)}
              >
                <span
                  className={`inline-block h-2 w-2 rounded-full ${HEALTH_DOT[health]} ${
                    !equipmentReady ? "animate-pulse" : ""
                  }`}
                  aria-hidden
                />
                {section.title}
                <span className={active ? "text-sky-600 dark:text-sky-300" : "text-ink-subtle dark:text-slate-500"}>
                  {section.equipment.length}
                </span>
              </button>
            );
          })}
          <button
            type="button"
            role="tab"
            aria-selected={showNone}
            onClick={() => selectPlatform(NONE)}
            className={pillClass(showNone)}
            title="Show nothing"
          >
            None
          </button>
        </div>
      </div>

      {/* Equipment pills for the selected platform, collapsed by default so
          only the platform pills stay visible. Expand to toggle single tiles
          or use the All / None shortcuts. Inside the sticky block above. */}
      {selected && (
        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={() => {
              setEquipmentOpen((o) => {
                const next = !o;
                window.sessionStorage.setItem(EQUIPMENT_OPEN_KEY, next ? "1" : "0");
                return next;
              });
            }}
            aria-expanded={equipmentOpen}
            aria-controls="equipment-pills"
            className={pillClass(equipmentOpen, "green")}
            title={equipmentOpen ? "Hide equipment filters" : "Show equipment filters"}
          >
            <span
              aria-hidden
              className={`inline-block transition-transform ${equipmentOpen ? "rotate-90" : ""}`}
            >
              ▸
            </span>
            Equipment filters
          </button>
          {equipmentOpen && (
            <div
              id="equipment-pills"
              className="flex flex-wrap items-center gap-1.5"
              role="group"
              aria-label="Toggle equipment tiles"
            >
              <button
                type="button"
                onClick={() => setHidden(new Set())}
                className={pillClass(hidden.size === 0, "green")}
                title="Show every tile"
              >
                All
              </button>
              <button
                type="button"
                onClick={() => setHidden(new Set(selected.equipment))}
                className={pillClass(hidden.size >= selected.equipment.length, "green")}
                title="Hide every tile"
              >
                None
              </button>
              {selected.equipment.map((id) => {
                const snap = snapshotById.get(id);
                const state = snap?.status?.equipment_status ?? "unknown";
                const visible = !hidden.has(id);
                return (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={visible}
                    onClick={() => toggleEquipment(id)}
                    className={equipmentPillClass(state, visible)}
                    title={visible ? "Click to hide this tile" : "Click to show this tile"}
                  >
                    {snap?.name ?? id}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
      </div>

      {showNone || !selected ? null : !equipmentReady ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: Math.min(selected.equipment.length || 4, 8) }, (_, i) => (
            <div
              key={i}
              className="h-[220px] animate-pulse rounded-xl bg-slate-200 dark:bg-slate-800"
            />
          ))}
        </div>
      ) : visibleSnapshots.length === 0 ? null : (
        <EquipmentGrid snapshots={visibleSnapshots} />
      )}
    </div>
  );
}
