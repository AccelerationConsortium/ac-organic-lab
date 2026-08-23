"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { getCameraMedia, mediaUrlForBrowser } from "@/lib/api";
import type { MediaFile } from "@/types/api";

/**
 * Minimal media gallery: server-rendered list of snapshots + recordings
 * with one link per file. Designed to ship today; a richer thumbnailing
 * gallery can replace it later without changing the URL.
 */
export default function CameraMediaPage({
  params,
}: {
  params: { platform: string; cameraId: string };
}) {
  const { platform, cameraId } = params;
  const { data, error, isPending } = useQuery({
    queryKey: ["camera-media", cameraId],
    queryFn: () => getCameraMedia(cameraId),
    refetchInterval: 5_000,
  });

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <Link
          href={`/platforms/${platform}`}
          className="text-xs text-ink-subtle hover:underline dark:text-slate-400"
        >
          ← Back to {platform.toUpperCase()} platform
        </Link>
        <h2 className="text-lg font-semibold text-ink dark:text-slate-100">
          {cameraId} · captures
        </h2>
        <p className="text-sm text-ink-muted dark:text-slate-300">
          Snapshots and recordings stored on the gateway server. Links open
          the file inline; right-click to download.
        </p>
      </header>

      {isPending && (
        <p className="text-sm text-ink-muted dark:text-slate-300">Loading…</p>
      )}
      {error && (
        <p className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-200">
          Failed to load media list: {(error as Error).message}
        </p>
      )}
      {data && (
        <>
          <Section
            title={`Snapshots (${data.snapshots.length})`}
            files={data.snapshots}
            cameraId={cameraId}
            emptyMessage="No snapshots yet — use the Snapshot button on the camera tile."
          />
          <Section
            title={`Recordings (${data.recordings.length})`}
            files={data.recordings}
            cameraId={cameraId}
            emptyMessage="No recordings yet."
          />
        </>
      )}
    </div>
  );
}

function Section({
  title,
  files,
  cameraId,
  emptyMessage,
}: {
  title: string;
  files: MediaFile[];
  cameraId: string;
  emptyMessage: string;
}) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold text-ink dark:text-slate-100">
        {title}
      </h3>
      {files.length === 0 ? (
        <p className="text-xs text-ink-subtle dark:text-slate-400">
          {emptyMessage}
        </p>
      ) : (
        <ul className="divide-y divide-slate-100 rounded-md border border-slate-200 bg-surface-raised dark:divide-slate-800 dark:border-slate-800 dark:bg-slate-900">
          {files.map((file) => (
            <li
              key={file.url}
              className="flex items-center justify-between gap-3 px-3 py-2 text-xs"
            >
              <a
                href={mediaUrlForBrowser(cameraId, file.url)}
                target="_blank"
                rel="noreferrer"
                className="min-w-0 flex-1 truncate font-mono text-sky-700 hover:underline dark:text-sky-300"
              >
                {file.lens}/{file.name}
              </a>
              <span className="shrink-0 tabular-nums text-ink-subtle dark:text-slate-400">
                {humanBytes(file.bytes)}
              </span>
              <span className="shrink-0 text-ink-subtle dark:text-slate-400">
                {formatTimestamp(file.mtime)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function humanBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}
