"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  cancelRecording,
  deletePreset,
  gotoPreset,
  mediaUrlForBrowser,
  postPtz,
  savePreset,
  setPrivacy,
  setStreaming,
  startRecording,
  startRolling,
  stopRecording,
  stopRolling,
  takeSnapshot,
} from "@/lib/api";
import type {
  CameraDetails,
  EquipmentSnapshot,
  LensStatusEntry,
  PresetEntry,
  PtzDirection,
  SnapshotResponse,
} from "@/types/api";

import { CameraPlayer } from "./CameraPlayer";
import { PtzPad } from "./PtzPad";
import { StalenessIndicator } from "./StalenessIndicator";
import { StatusPill } from "./StatusPill";

type CameraStatusDetails = CameraDetails & Record<string, unknown>;

/**
 * A self-contained camera tile.
 *
 * Layout (top to bottom):
 *
 *   - header: name + status pill (lens tabs stack under pill, right-aligned)
 *   - <CameraPlayer> for the active lens's feed (MSE on desktop, WebRTC
 *     on iPhone; absorbs vertical slack)
 *   - control row, three columns side-by-side:
 *       1. <PtzPad> for live pan/tilt
 *       2. preset column: dropdown + "Save current view as..."
 *       3. capture column: snapshot, record/stop, "Recent captures ->"
 *   - toggle row: Streaming · Privacy · staleness
 *   - last-snapshot toast + status message banner (when present)
 */
export function CameraTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const queryClient = useQueryClient();

  const details = (snapshot.status.details ?? {}) as CameraStatusDetails;
  const lenses: LensStatusEntry[] = Array.isArray(details.lenses) ? details.lenses : [];
  const presets: PresetEntry[] = Array.isArray(details.presets) ? details.presets : [];

  const onvifReachable = Boolean(details.onvif_reachable);
  const tapoReachable = Boolean(details.tapo_reachable);
  const streamingEnabled = details.streaming_enabled !== false;
  const privacyMode = Boolean(details.privacy_mode);

  const [activeLensId, setActiveLensId] = useState<string | null>(null);
  const activeLens = useMemo(() => {
    if (lenses.length === 0) return null;
    return lenses.find((lens) => lens.id === activeLensId) ?? lenses[0];
  }, [activeLensId, lenses]);

  // Rolling state comes from the active lens entry (declared after activeLens).
  const rollingActive = Boolean(activeLens?.rolling_active);
  const rollingSegmentCount = activeLens?.rolling_segment_count ?? 0;

  // PTZ is only available when ONVIF is reachable AND the active lens has
  // a PTZ motor. Fixed lenses (e.g. wide on C245D) carry ptz_capable: false
  // in equipment.yaml; we cross-reference via snapshot.camera?.lenses.
  const activeLensConfig = snapshot.camera?.lenses?.find(
    (l) => l.id === activeLens?.id,
  );
  const ptzCapable = onvifReachable && (activeLensConfig?.ptz_capable !== false);

  const [presetSelection, setPresetSelection] = useState<string>("");
  const [presetModalOpen, setPresetModalOpen] = useState(false);
  const [presetName, setPresetName] = useState("");
  const [lastSnapshot, setLastSnapshot] = useState<SnapshotResponse | null>(null);

  // We don't currently track the recording_id on the client side -
  // the gateway only allows a single active recording per (camera,
  // lens) anyway, so passing `recording_id` is unnecessary; the stop
  // / cancel calls fall back to the camera's only active recording.
  const recordingActive = activeLens?.recording_active === true;

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["equipment"] });
  const onError = (err: unknown) => {
    if (typeof window !== "undefined") {
      window.alert(`Camera control failed: ${(err as Error).message}`);
    }
  };

  const ptzMutation = useMutation({
    mutationFn: (direction: PtzDirection) =>
      direction === "stop"
        ? postPtz(snapshot.id, { pan: 0, tilt: 0, zoom: 0 })
        : postPtz(snapshot.id, { direction, speed: 0.5, duration_ms: 1500 }),
    onError,
  });
  const stopMutation = useMutation({
    mutationFn: () => postPtz(snapshot.id, { pan: 0, tilt: 0, zoom: 0 }),
    onError,
  });
  const gotoMutation = useMutation({
    mutationFn: (preset_id: string) => gotoPreset(snapshot.id, { preset_id }),
    onSuccess: invalidate,
    onError,
  });
  const saveMutation = useMutation({
    mutationFn: (name: string) => savePreset(snapshot.id, { name }),
    onSuccess: () => {
      setPresetModalOpen(false);
      setPresetName("");
      invalidate();
    },
    onError,
  });
  const deleteMutation = useMutation({
    mutationFn: (preset_id: string) => deletePreset(snapshot.id, preset_id),
    onSuccess: () => {
      setPresetSelection("");
      invalidate();
    },
    onError,
  });
  const privacyMutation = useMutation({
    mutationFn: (enabled: boolean) => setPrivacy(snapshot.id, { enabled }),
    onSuccess: invalidate,
    onError,
  });
  const streamingMutation = useMutation({
    mutationFn: (enabled: boolean) => setStreaming(snapshot.id, { enabled }),
    onSuccess: invalidate,
    onError,
  });

  const rollingMutation = useMutation({
    mutationFn: (enable: boolean) =>
      enable
        ? startRolling(snapshot.id, { lens: activeLens?.id, segment_duration_s: 1800, max_segments: 96 })
        : stopRolling(snapshot.id),
    onSuccess: invalidate,
    onError,
  });

  const snapshotMutation = useMutation({
    mutationFn: (lens: string | undefined) => takeSnapshot(snapshot.id, { lens }),
    onSuccess: (response) => {
      setLastSnapshot(response);
      invalidate();
    },
    onError,
  });
  const recordStartMutation = useMutation({
    mutationFn: (lens: string | undefined) => startRecording(snapshot.id, { lens }),
    onSuccess: invalidate,
    onError,
  });
  const recordStopMutation = useMutation({
    mutationFn: () => stopRecording(snapshot.id, {}),
    onSuccess: invalidate,
    onError,
  });
  const recordCancelMutation = useMutation({
    mutationFn: () => cancelRecording(snapshot.id, {}),
    onSuccess: invalidate,
    onError,
  });

  return (
    <article className="flex h-full flex-col gap-3 rounded-xl border border-slate-200 bg-surface-raised p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      {/*
        Header: name + id on the left; status pill and lens tabs stacked
        right-aligned on the right. Putting the lens tabs under the
        status pill keeps the header compact (one logical row) and lines
        the secondary controls up vertically.
      */}
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-base font-semibold text-ink dark:text-slate-100">
            {snapshot.name}
          </h3>
          <p className="text-xs text-ink-subtle dark:text-slate-500">
            <span className="font-mono">{snapshot.id}</span>
            {snapshot.camera?.host ? <> · {snapshot.camera.host}</> : null}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <StatusPill state={snapshot.status.equipment_status} />
          {lenses.length > 1 && (
            <div className="flex gap-1">
              {lenses.map((lens) => {
                const isActive = lens.id === activeLens?.id;
                return (
                  <button
                    key={lens.id}
                    type="button"
                    onClick={() => setActiveLensId(lens.id)}
                    className={`shrink-0 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                      isActive
                        ? "border-sky-500 bg-sky-50 text-sky-900 dark:border-sky-400 dark:bg-sky-900/40 dark:text-sky-100"
                        : "border-slate-200 text-ink-muted hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
                    }`}
                  >
                    {lens.label}
                    <span
                      className={`ml-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${
                        lens.stream_connected ? "bg-emerald-500" : "bg-slate-400"
                      }`}
                    />
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </header>

      {/*
        The grid that hosts this tile uses fixed-height rows (see
        `EquipmentGrid`), so the article reliably gets more vertical
        space than the natural content height. Letting the video absorb
        the surplus (`flex-1 min-h-0`) keeps the 16:9 frame centered and
        the toggle row pinned to the bottom of the card - no awkward
        gap below the controls.
      */}
      <CameraPlayer
        src={activeLens?.mse_url ?? null}
        disabled={!streamingEnabled || privacyMode}
        className="flex-1 min-h-0 w-full"
      />

      {/*
        Below-video controls: three columns side-by-side.
        - Left: PTZ pad pinned at its natural ~7.5rem size.
        - Middle: preset selector column (flex-1, shrinks gracefully).
        - Right: capture column (snapshot, record/stop, "Recent ->").
        Capture column is pinned shrink-0 so the buttons keep the same
        width regardless of how cramped the preset row gets.
      */}
      <div className="flex items-start gap-3">
        <PtzPad
          disabled={!ptzCapable}
          onMove={(direction) => ptzMutation.mutate(direction)}
          onStop={() => stopMutation.mutate()}
        />

        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex items-center gap-1.5">
            <select
              value={presetSelection}
              onChange={(event) => setPresetSelection(event.target.value)}
              disabled={!ptzCapable || presets.length === 0}
              className="min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-ink disabled:bg-slate-50 disabled:text-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:disabled:bg-slate-900 dark:disabled:text-slate-600"
            >
              <option value="">
                {presets.length === 0 ? "(no presets)" : "Select preset…"}
              </option>
              {presets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={!ptzCapable || !presetSelection}
              onClick={() => presetSelection && gotoMutation.mutate(presetSelection)}
              className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-ink hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:text-slate-100 dark:hover:bg-slate-700"
            >
              Go
            </button>
            <button
              type="button"
              disabled={!ptzCapable || !presetSelection}
              onClick={() => presetSelection && deleteMutation.mutate(presetSelection)}
              className="rounded-md border border-rose-200 px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-40 dark:border-rose-900/60 dark:text-rose-300 dark:hover:bg-rose-900/20"
            >
              ✕
            </button>
          </div>
          <button
            type="button"
            disabled={!ptzCapable}
            onClick={() => setPresetModalOpen(true)}
            className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-ink hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:text-slate-100 dark:hover:bg-slate-700"
          >
            + Save current view as…
          </button>
        </div>

        {/*
          Capture column: snapshot + record/stop, with a "Recent
          captures ->" link beneath. Live for the active lens so the
          buttons match what the user is looking at.
        */}
        <div className="flex w-32 shrink-0 flex-col gap-2">
          <button
            type="button"
            disabled={
              !activeLens ||
              !streamingEnabled ||
              privacyMode ||
              snapshotMutation.isPending
            }
            onClick={() => snapshotMutation.mutate(activeLens?.id)}
            className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-ink hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:text-slate-100 dark:hover:bg-slate-700"
          >
            {snapshotMutation.isPending ? "Capturing…" : "📷 Snapshot"}
          </button>
          {recordingActive ? (
            <div className="flex items-stretch gap-1">
              <button
                type="button"
                disabled={recordStopMutation.isPending}
                onClick={() => recordStopMutation.mutate()}
                className="flex flex-1 items-center justify-center rounded-md border border-rose-500 bg-rose-500 px-2 py-1 text-xs font-medium text-white hover:bg-rose-600 disabled:opacity-40"
              >
                <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-white align-middle" />
                {recordStopMutation.isPending ? "Stopping…" : "Stop"}
              </button>
              <button
                type="button"
                disabled={recordCancelMutation.isPending}
                onClick={() => recordCancelMutation.mutate()}
                className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-40 dark:border-slate-700 dark:text-rose-300 dark:hover:bg-rose-900/20"
                title="Discard the in-progress recording"
              >
                ✕
              </button>
            </div>
          ) : (
            <button
              type="button"
              disabled={
                !activeLens ||
                !streamingEnabled ||
                privacyMode ||
                recordStartMutation.isPending
              }
              onClick={() => recordStartMutation.mutate(activeLens?.id)}
              className="rounded-md border border-rose-300 px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-40 dark:border-rose-900/60 dark:text-rose-300 dark:hover:bg-rose-900/20"
            >
              {recordStartMutation.isPending ? "Starting…" : "● Record"}
            </button>
          )}
          <Link
            href={`/platforms/${snapshot.platform}/media/${snapshot.id}`}
            className="text-right text-[11px] text-ink-subtle underline-offset-2 hover:underline dark:text-slate-500"
          >
            Recent captures →
          </Link>
        </div>
      </div>

      <div className="flex items-center gap-4 border-t border-slate-100 pt-2 dark:border-slate-800">
        <Toggle
          label="Streaming"
          checked={streamingEnabled}
          disabled={streamingMutation.isPending}
          onChange={(value) => streamingMutation.mutate(value)}
        />
        <Toggle
          label="Privacy"
          checked={privacyMode}
          disabled={!tapoReachable || privacyMutation.isPending}
          onChange={(value) => privacyMutation.mutate(value)}
        />
        <Toggle
          label="Rolling"
          checked={rollingActive}
          disabled={rollingMutation.isPending}
          title={rollingActive ? `${rollingSegmentCount} segment(s) on disk · click to stop` : "Start rolling recorder (30 min segments, keep 96)"}
          onChange={(value) => rollingMutation.mutate(value)}
        />
        <span className="ml-auto flex items-center gap-2 text-[11px] text-ink-subtle dark:text-slate-500">
          {snapshot.latency_ms != null && (
            <span
              className={
                snapshot.latency_ms >= 500
                  ? "text-amber-700 dark:text-amber-400"
                  : undefined
              }
              title={snapshot.latency_ms >= 500 ? "Slow poll (>=500 ms)" : undefined}
            >
              {snapshot.latency_ms} ms
            </span>
          )}
          <StalenessIndicator fetchedAt={snapshot.fetched_at} />
        </span>
      </div>

      {lastSnapshot && (
        <a
          href={mediaUrlForBrowser(snapshot.id, lastSnapshot.url)}
          target="_blank"
          rel="noreferrer"
          className="block rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 text-[11px] font-medium text-emerald-900 hover:bg-emerald-100 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-200 dark:hover:bg-emerald-900/30"
        >
          ✓ Saved {lastSnapshot.lens} snapshot · {humanBytes(lastSnapshot.bytes)}
          <span className="ml-1 underline">open</span>
        </a>
      )}

      {snapshot.status.message && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200">
          {snapshot.status.message}
        </p>
      )}

      {presetModalOpen && (
        <PresetModal
          name={presetName}
          onChange={setPresetName}
          saving={saveMutation.isPending}
          onCancel={() => {
            setPresetModalOpen(false);
            setPresetName("");
          }}
          onSave={() => {
            const trimmed = presetName.trim();
            if (trimmed) saveMutation.mutate(trimmed);
          }}
        />
      )}
    </article>
  );
}

function Toggle({
  label,
  checked,
  disabled,
  title,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  title?: string;
  onChange: (value: boolean) => void;
}) {
  return (
    <label
      title={title}
      className={`inline-flex cursor-pointer items-center gap-2 text-xs ${
        disabled ? "cursor-not-allowed opacity-50" : ""
      }`}
    >
      <span className="text-ink-muted dark:text-slate-400">{label}</span>
      <span
        className={`relative inline-block h-4 w-7 rounded-full transition-colors ${
          checked ? "bg-sky-500" : "bg-slate-300 dark:bg-slate-700"
        }`}
        aria-hidden
      >
        <span
          className={`absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-all ${
            checked ? "left-3.5" : "left-0.5"
          }`}
        />
      </span>
      <input
        type="checkbox"
        className="sr-only"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
    </label>
  );
}

function humanBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return "?";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function PresetModal({
  name,
  onChange,
  saving,
  onCancel,
  onSave,
}: {
  name: string;
  onChange: (value: string) => void;
  saving: boolean;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-surface-raised p-4 shadow-xl dark:border-slate-800 dark:bg-slate-900">
        <h4 className="text-sm font-semibold text-ink dark:text-slate-100">Save preset</h4>
        <p className="mt-1 text-xs text-ink-muted dark:text-slate-400">
          Captures the camera's current pan/tilt/zoom under the given name.
        </p>
        <input
          autoFocus
          value={name}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") onSave();
            if (event.key === "Escape") onCancel();
          }}
          maxLength={64}
          placeholder="e.g. balance front"
          className="mt-3 w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-ink dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
        />
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            className="rounded-md border border-slate-300 px-3 py-1 text-xs font-medium text-ink hover:bg-slate-50 dark:border-slate-700 dark:text-slate-100 dark:hover:bg-slate-700"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={saving || !name.trim()}
            className="rounded-md bg-sky-600 px-3 py-1 text-xs font-medium text-white hover:bg-sky-500 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
