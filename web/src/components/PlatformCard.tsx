"use client";

import { useState } from "react";
import Link from "next/link";
import type {
  CameraDetails,
  EquipmentSnapshot,
  LensStatusEntry,
} from "@/types/api";
import { kindLabel } from "@/lib/format";
import { AuthGatedLink } from "./AuthGatedLink";
import { CameraPlayer } from "./CameraPlayer";
import { StatusDots } from "./StatusDots";
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
        <span className="text-xs uppercase tracking-wider">Stream hidden</span>
        <span className="text-[10px] text-slate-600">{label}</span>
      </div>
    </div>
  );
}

/**
 * Live preview for the platform's camera (if any). Picks the first lens,
 * disables the player when the camera is in privacy mode or has streaming
 * turned off, and overlays the camera name + a deep link to `/cameras` for
 * full PTZ/preset controls. Falls back to the placeholder if the camera is
 * not yet ready (no lens, no MSE URL).
 */
function PlatformCameraPreview({ camera }: { camera: EquipmentSnapshot }) {
  const details = (camera.status.details ?? {}) as Partial<CameraDetails>;
  const lenses: LensStatusEntry[] = Array.isArray(details.lenses)
    ? details.lenses
    : [];
  const lens = lenses[0] ?? null;
  const streamingEnabled = details.streaming_enabled !== false;
  const privacyMode = Boolean(details.privacy_mode);

  const lensLabel = lens?.label ?? "Camera";

  return (
    <div className="relative">
      {lens?.mse_url ? (
        <CameraPlayer
          src={lens.mse_url}
          disabled={!streamingEnabled || privacyMode}
        />
      ) : (
        <VideoFeedPlaceholder label={camera.id} />
      )}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-2 p-2">
        <div className="pointer-events-auto flex items-center gap-1.5 rounded-md bg-slate-900/70 px-2 py-0.5 backdrop-blur-sm">
          <span className="text-[10px] font-medium uppercase tracking-wider text-slate-100">
            {camera.name} · {lensLabel}
          </span>
          <StatusPill state={camera.status.equipment_status} />
        </div>
        <Link
          href={`/platforms/${camera.platform}`}
          className="pointer-events-auto rounded-md bg-slate-900/70 px-2 py-0.5 text-[10px] font-medium text-orange-300 backdrop-blur-sm hover:text-orange-200"
        >
          GO →
        </Link>
      </div>
    </div>
  );
}

function EquipmentRowSkeleton() {
  return (
    <li className="flex items-center justify-between gap-2 rounded-md border border-slate-100 bg-white/60 px-2.5 py-1.5 dark:border-slate-800 dark:bg-slate-950/40">
      <div className="h-3.5 w-24 animate-pulse rounded bg-slate-200 dark:bg-slate-800" />
      <div className="h-3.5 w-12 animate-pulse rounded-full bg-slate-200 dark:bg-slate-800" />
    </li>
  );
}

// Health + activity for an equipment row are the shared two-dot indicator
// (StatusDots), identical to the History → Uptime table.

function EquipmentRow({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const showOpen = snapshot.pill?.open === true && !!snapshot.base_url;
  const linkLabel = snapshot.pill?.link_label;
  const linkHref = snapshot.pill?.link_href;
  const showLink = !!linkLabel && !!linkHref;
  const internalLink = snapshot.pill?.internal === true;
  // authorized_only pills vanish for viewers without a role on this
  // equipment instead of rendering a click-blocked link.
  const hideUnauthorized = snapshot.pill?.authorized_only === true;
  return (
    <li className="flex items-center justify-between gap-2 rounded-md border border-slate-100 bg-white/60 px-2.5 py-1.5 dark:border-slate-800 dark:bg-slate-950/40">
      <div
        className="min-w-0 flex-1 truncate text-sm font-medium text-ink dark:text-slate-100"
        title={kindLabel(snapshot.kind)}
      >
        {snapshot.name}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {showLink &&
          (internalLink ? (
            <Link
              href={linkHref!}
              className="text-[10px] font-medium text-orange-600 hover:underline dark:text-orange-400"
            >
              GO →
            </Link>
          ) : (
            <AuthGatedLink
              href={linkHref!}
              equipmentId={snapshot.id}
              external
              hideUnauthorized={hideUnauthorized}
              className="text-[10px] font-medium text-orange-600 hover:underline dark:text-orange-400"
            >
              Open ↗
            </AuthGatedLink>
          ))}
        {showOpen && (
          <AuthGatedLink
            href={snapshot.base_url!}
            equipmentId={snapshot.id}
            external
            hideUnauthorized={hideUnauthorized}
            className="text-[10px] font-medium text-orange-600 hover:underline dark:text-orange-400"
          >
            Open ↗
          </AuthGatedLink>
        )}
        <StatusDots snapshot={snapshot} />
      </div>
    </li>
  );
}

export function PlatformCard({
  id,
  title,
  description,
  href,
  snapshots,
  pending = false,
  expectedCount,
}: {
  id: string;
  title: string;
  description?: string;
  href?: string;
  snapshots: EquipmentSnapshot[];
  // When the equipment list hasn't loaded yet, render skeleton rows instead of
  // blocking the whole page. ``expectedCount`` (the section's id count from
  // platforms.yaml) sizes the skeleton so the card doesn't reflow on arrival.
  pending?: boolean;
  expectedCount?: number;
}) {
  // The platform's camera (if any) drives the preview region and also remains
  // in the equipment list so its status is visible alongside the other modules.
  const camera = snapshots.find((s) => s.kind === "camera") ?? null;
  const [streamVisible, setStreamVisible] = useState(false);

  const showSkeleton = pending && snapshots.length === 0;
  const count = snapshots.length > 0 ? snapshots.length : (expectedCount ?? 0);

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
        <div className="flex shrink-0 items-center gap-2">
          {camera && (
            <button
              onClick={() => setStreamVisible((v) => !v)}
              title={streamVisible ? "Hide camera stream" : "Show camera stream"}
              className="rounded-md border border-slate-200 bg-white/60 px-2 py-0.5 text-[10px] font-medium text-ink-subtle hover:bg-white dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-slate-700"
            >
              {streamVisible ? "Hide stream" : "Show stream"}
            </button>
          )}
          {href && (
            <Link
              href={href}
              className="text-xs font-medium text-orange-600 hover:underline dark:text-orange-400"
            >
              GO →
            </Link>
          )}
        </div>
      </header>

      {camera &&
        (streamVisible ? (
          <PlatformCameraPreview camera={camera} />
        ) : (
          <VideoFeedPlaceholder label={camera.name} />
        ))}

      <div>
        <h4 className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-subtle dark:text-slate-500">
          Equipment ({count})
        </h4>
        {showSkeleton ? (
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {Array.from({ length: Math.max(count, 1) }).map((_, i) => (
              <EquipmentRowSkeleton key={i} />
            ))}
          </ul>
        ) : snapshots.length === 0 ? (
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
