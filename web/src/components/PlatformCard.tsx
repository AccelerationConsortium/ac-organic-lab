import Link from "next/link";
import type { EquipmentSnapshot } from "@/types/api";
import { kindLabel } from "@/lib/format";
import { StatusPill } from "./StatusPill";

function VideoFeedPlaceholder({ label }: { label: string }) {
  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-md border border-slate-200 bg-slate-900 dark:border-slate-700">
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-slate-500">
        <svg
          className="h-10 w-10 opacity-50"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <rect x="2" y="6" width="14" height="12" rx="2" />
          <path d="M16 10l5-3v10l-5-3z" />
        </svg>
        <span className="text-xs uppercase tracking-wider">
          Video feed placeholder
        </span>
        <span className="text-[10px] text-slate-600">{label}</span>
      </div>
    </div>
  );
}

function EquipmentRow({ snapshot }: { snapshot: EquipmentSnapshot }) {
  return (
    <li className="flex items-center justify-between gap-2 rounded-md border border-slate-100 bg-white/60 px-2.5 py-1.5 dark:border-slate-800 dark:bg-slate-950/40">
      <div
        className="min-w-0 flex-1 truncate text-sm font-medium text-ink dark:text-slate-100"
        title={kindLabel(snapshot.kind)}
      >
        {snapshot.name}
      </div>
      <StatusPill state={snapshot.status.equipment_status} />
    </li>
  );
}

export function PlatformCard({
  id,
  title,
  description,
  href,
  snapshots,
}: {
  id: string;
  title: string;
  description?: string;
  href?: string;
  snapshots: EquipmentSnapshot[];
}) {
  return (
    <article className="flex flex-col gap-4 rounded-xl border border-slate-200 bg-surface-raised p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-ink dark:text-slate-100">
            {title}
          </h3>
          {description && (
            <p className="text-xs text-ink-subtle dark:text-slate-500">
              {description}
            </p>
          )}
        </div>
        {href && (
          <Link
            href={href}
            className="shrink-0 text-xs font-medium text-sky-700 hover:underline dark:text-sky-400"
          >
            Open →
          </Link>
        )}
      </header>

      <VideoFeedPlaceholder label={id} />

      <div>
        <h4 className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-subtle dark:text-slate-500">
          Equipment ({snapshots.length})
        </h4>
        {snapshots.length === 0 ? (
          <p className="text-sm text-ink-subtle dark:text-slate-500">
            No equipment registered for this platform.
          </p>
        ) : (
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {snapshots.map((s) => (
              <EquipmentRow key={s.id} snapshot={s} />
            ))}
          </ul>
        )}
      </div>
    </article>
  );
}
