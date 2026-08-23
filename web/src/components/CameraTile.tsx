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

import { ackFailureMessage } from "@/lib/control-ack";
import { useActionError } from "@/lib/use-action-error";
import { useUserAuth } from "@/lib/user-auth";

import { CameraPlayer } from "./CameraPlayer";
import { PtzPad } from "./PtzPad";
import { StatusPill } from "./StatusPill";
import { TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

type CameraStatusDetails = CameraDetails & Record<string, unknown>;

/**
 * A camera tile on the shared <TileShell> template.
 *
 * Template mapping:
 *
 *   - lifecycle ON/OFF = streaming on/off (a camera's "power" from the
 *     dashboard's perspective is whether it is streaming)
 *   - banner extras: Privacy + Rolling toggles, lens tabs pushed right
 *   - body: <CameraPlayer> (absorbs vertical slack), then the control row
 *     (PtzPad · preset column · capture column)
 *   - footer (message, latency, staleness) comes from the template
 */
export function CameraTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const queryClient = useQueryClient();
  const { authenticated, canControl } = useUserAuth();
  const authorized = authenticated && canControl(snapshot.id);

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

  const { actionError, reportError, clearError } = useActionError();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["equipment"] });
  // Surface control failures in the shared inline band (same as every other
  // tile) instead of a blocking window.alert.
  const onError = (err: unknown) => reportError(err);
  // The gateway can soft-fail with 200 + ok:false (e.g. "pan limit reached"
  // when the PTZ head is at its physical limit). controlPost only throws on
  // non-2xx, so success handlers must check the ack; returns true when the
  // action really succeeded. On a genuine success we also clear any stale
  // band — otherwise a limit warning would linger after the operator moves
  // the head back off the limit.
  const guardAck = (ack: unknown): boolean => {
    const failure = ackFailureMessage(ack);
    if (failure !== null) {
      reportError(new Error(failure));
      return false;
    }
    clearError();
    return true;
  };

  const ptzMutation = useMutation({
    mutationFn: (direction: PtzDirection) =>
      direction === "stop"
        ? postPtz(snapshot.id, { pan: 0, tilt: 0, zoom: 0 })
        : postPtz(snapshot.id, { direction, speed: 0.5, duration_ms: 1500 }),
    // Clear the band the instant a new move starts (matches useActionError's
    // exec pattern), so pressing away from a limit drops the warning without
    // waiting for the nudge to finish.
    onMutate: clearError,
    onSuccess: guardAck,
    onError,
  });
  const stopMutation = useMutation({
    mutationFn: () => postPtz(snapshot.id, { pan: 0, tilt: 0, zoom: 0 }),
    onMutate: clearError,
    onSuccess: guardAck,
    onError,
  });
  const gotoMutation = useMutation({
    mutationFn: (preset_id: string) => gotoPreset(snapshot.id, { preset_id }),
    onSuccess: (ack) => {
      guardAck(ack);
      invalidate();
    },
    onError,
  });
  const saveMutation = useMutation({
    mutationFn: (name: string) => savePreset(snapshot.id, { name }),
    onSuccess: (ack) => {
      if (!guardAck(ack)) return; // keep the modal open so the user can retry
      setPresetModalOpen(false);
      setPresetName("");
      invalidate();
    },
    onError,
  });
  const deleteMutation = useMutation({
    mutationFn: (preset_id: string) => deletePreset(snapshot.id, preset_id),
    onSuccess: (ack) => {
      if (!guardAck(ack)) return;
      setPresetSelection("");
      invalidate();
    },
    onError,
  });
  const privacyMutation = useMutation({
    mutationFn: (enabled: boolean) => setPrivacy(snapshot.id, { enabled }),
    onSuccess: (ack) => {
      guardAck(ack);
      invalidate();
    },
    onError,
  });
  const streamingMutation = useMutation({
    mutationFn: (enabled: boolean) => setStreaming(snapshot.id, { enabled }),
    onSuccess: (ack) => {
      guardAck(ack);
      invalidate();
    },
    onError,
  });

  const rollingMutation = useMutation({
    mutationFn: (enable: boolean) =>
      enable
        ? startRolling(snapshot.id, { lens: activeLens?.id, segment_duration_s: 1800, max_segments: 96 })
        : stopRolling(snapshot.id),
    onSuccess: (ack) => {
      guardAck(ack);
      invalidate();
    },
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
    <TileShell
      snapshot={snapshot}
      headerRight={<StatusPill state={snapshot.status.equipment_status} />}
      actionError={actionError}
      subtitleExtra={
        snapshot.camera?.host ? (
          <span className="font-mono">{snapshot.camera.host}</span>
        ) : undefined
      }
      lifecycle={{
        // The template's power toggle IS the streaming switch: a camera is
        // "on" for the dashboard when it streams.
        isOn: streamingEnabled,
        onPowerToggle: () => streamingMutation.mutate(!streamingEnabled),
        disabled: !authorized || streamingMutation.isPending,
        powerTitle: !authorized
          ? "Sign in to control this camera"
          : streamingEnabled
            ? "Streaming on — click to turn off"
            : "Streaming off — click to turn on",
      }}
      bannerExtra={
        <>
          <Toggle
            label="Privacy"
            checked={privacyMode}
            disabled={!authorized || !tapoReachable || privacyMutation.isPending}
            onChange={(value) => privacyMutation.mutate(value)}
          />
          <Toggle
            label="Rolling"
            checked={rollingActive}
            disabled={!authorized || rollingMutation.isPending}
            title={
              rollingActive
                ? `${rollingSegmentCount} segment(s) on disk · click to stop`
                : "Start rolling recorder (30 min segments, keep 96)"
            }
            onChange={(value) => rollingMutation.mutate(value)}
          />
          {lenses.length > 1 && (
            <div className="ml-auto flex gap-1">
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
                        : "border-slate-200 text-ink-muted hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
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
        </>
      }
    >
      {/*
        The grid that hosts this tile uses fixed-height rows (see
        `EquipmentGrid`), so the article reliably gets more vertical
        space than the natural content height. Letting the video absorb
        the surplus (`flex-1 min-h-0`) keeps the 16:9 frame centered and
        the control row pinned above the template footer - no awkward
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
      {!authorized && (
        <p className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] text-ink-muted dark:border-slate-700 dark:bg-slate-800/40 dark:text-slate-300">
          {authenticated
            ? "View-only — no access to this camera."
            : "View-only — sign in to control this camera."}
        </p>
      )}
      {/* All camera controls require a signed-in session with a role on this
          equipment. A disabled <fieldset> (display:contents → no layout
          change) natively disables every nested button / select / toggle. */}
      <fieldset disabled={!authorized} className="contents">
      <div className="flex items-start gap-3">
        <PtzPad
          disabled={!ptzCapable || !authorized}
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
            <TileButton
              disabled={!ptzCapable || !presetSelection}
              onClick={() => presetSelection && gotoMutation.mutate(presetSelection)}
            >
              Go
            </TileButton>
            <TileButton
              variant="danger"
              ariaLabel="Delete preset"
              disabled={!ptzCapable || !presetSelection}
              onClick={() => presetSelection && deleteMutation.mutate(presetSelection)}
            >
              ✕
            </TileButton>
          </div>
          <TileButton
            disabled={!ptzCapable}
            onClick={() => setPresetModalOpen(true)}
          >
            + Save current view as…
          </TileButton>
        </div>

        {/*
          Capture column: snapshot + record/stop, with a "Recent
          captures ->" link beneath. Live for the active lens so the
          buttons match what the user is looking at.
        */}
        <div className="flex w-32 shrink-0 flex-col gap-2">
          <TileButton
            disabled={
              !activeLens ||
              !streamingEnabled ||
              privacyMode ||
              snapshotMutation.isPending
            }
            onClick={() => snapshotMutation.mutate(activeLens?.id)}
          >
            {snapshotMutation.isPending ? "Capturing…" : "📷 Snapshot"}
          </TileButton>
          {recordingActive ? (
            <div className="flex items-stretch gap-1">
              <TileButton
                variant="danger"
                disabled={recordStopMutation.isPending}
                onClick={() => recordStopMutation.mutate()}
              >
                <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current align-middle" />
                {recordStopMutation.isPending ? "Stopping…" : "Stop"}
              </TileButton>
              <TileButton
                variant="danger"
                ariaLabel="Discard the in-progress recording"
                title="Discard the in-progress recording"
                disabled={recordCancelMutation.isPending}
                onClick={() => recordCancelMutation.mutate()}
              >
                ✕
              </TileButton>
            </div>
          ) : (
            <TileButton
              variant="danger"
              disabled={
                !activeLens ||
                !streamingEnabled ||
                privacyMode ||
                recordStartMutation.isPending
              }
              onClick={() => recordStartMutation.mutate(activeLens?.id)}
            >
              {recordStartMutation.isPending ? "Starting…" : "● Record"}
            </TileButton>
          )}
          <Link
            href={`/platforms/${snapshot.platform}/media/${snapshot.id}`}
            className="text-right text-[11px] text-ink-subtle underline-offset-2 hover:underline dark:text-slate-400"
          >
            Recent captures →
          </Link>
        </div>
      </div>
      </fieldset>

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
    </TileShell>
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
      <span className="text-ink-muted dark:text-slate-300">{label}</span>
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
        <p className="mt-1 text-xs text-ink-muted dark:text-slate-300">
          Captures the camera&apos;s current pan/tilt/zoom under the given name.
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
