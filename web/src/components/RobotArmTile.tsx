"use client";

import type { EquipmentSnapshot } from "@/types/api";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { FetchErrorBand } from "./FetchErrorBand";
import { StatusPill } from "./StatusPill";
import { TileShell } from "./TileShell";

type Tone = "ok" | "warn" | "muted" | "neutral";

const TONE_CLASSES: Record<Tone, string> = {
  ok: "border-emerald-300 bg-emerald-50 dark:border-emerald-700 dark:bg-emerald-950/40",
  warn: "border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/40",
  muted:
    "border-slate-200 bg-slate-100 dark:border-slate-700 dark:bg-slate-800/20",
  neutral:
    "border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/40",
};

function componentTone(state: string | undefined | null): Tone {
  if (!state) return "muted";
  if (state === "enabled" || state === "stable" || state === "idle") return "ok";
  if (state === "error" || state === "fault") return "warn";
  if (state === "disabled" || state === "disconnected" || state === "uncalibrated")
    return "muted";
  return "neutral";
}

function Pill({
  caption,
  value,
  tone = "neutral",
  title,
}: {
  caption?: string;
  value: string;
  tone?: Tone;
  title?: string;
}) {
  return (
    <div
      className={`flex h-7 items-center gap-1 rounded-md border px-2 ${TONE_CLASSES[tone]}`}
      title={title}
    >
      {caption ? (
        <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
          {caption}
        </span>
      ) : null}
      <span className="font-mono text-xs font-semibold text-ink dark:text-slate-100 tabular-nums">
        {value}
      </span>
    </div>
  );
}

function num(
  snapshot: EquipmentSnapshot,
  key: string,
): { value: number; unit: string | null } | null {
  const m = (snapshot.status.metrics ?? {})[key];
  if (!m || typeof m.value !== "number") return null;
  return { value: m.value, unit: m.unit ?? null };
}

function fmt(v: { value: number; unit: string | null } | null, decimals = 0) {
  if (!v) return "—";
  const unit = v.unit ? ` ${v.unit}` : "";
  return `${v.value.toFixed(decimals)}${unit}`;
}

export function RobotArmTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const { status } = snapshot;
  const components = status.components ?? {};
  const details = (status.details ?? {}) as Record<string, unknown>;
  const { locked, countdown, toggle } = useControlLock(snapshot.id);

  const arm = components["arm"];
  const gripper = components["gripper"];
  const track = components["track"];

  const tcpSpeed = num(snapshot, "tcp_speed");
  const angleSpeed = num(snapshot, "angle_speed");
  const trackPos = num(snapshot, "track_position");
  const ftForce = num(snapshot, "force_magnitude");

  // Gripper grip-force is published on the device's static config block; the
  // FT-sensor `metrics.force_magnitude` reading is a different signal (wrist
  // force/torque, currently disabled on the live xArm5). Prefer the FT
  // reading when the sensor is enabled, otherwise show the configured grip
  // force from gripper_config.
  const gripperConfig =
    (details["connection_details"] as Record<string, unknown> | undefined)?.[
      "gripper_config"
    ] as Record<string, unknown> | undefined;
  const configForce =
    gripperConfig && typeof gripperConfig["force"] === "number"
      ? (gripperConfig["force"] as number)
      : null;
  const ftEnabled = components["force_torque"]?.state === "enabled";

  // Current gripper position is not (yet) in /status; fall back to the
  // configured stroke range from gripper_config so the operator at least
  // sees the gripper's working envelope (71–150 mm on the BioGripper Gen2).
  // When the device repo starts publishing metrics.gripper_position, the
  // live value takes over automatically.
  const gripperPos = num(snapshot, "gripper_position");
  const strokeRange = gripperConfig?.["stroke_range"] as
    | { min?: number; max?: number }
    | undefined;
  const strokeMin =
    strokeRange && typeof strokeRange.min === "number" ? strokeRange.min : null;
  const strokeMax =
    strokeRange && typeof strokeRange.max === "number" ? strokeRange.max : null;

  // Track preset: motion_graph.rail_location_name is non-null when the track
  // is currently parked at a named rail location.
  const motionGraph = details["motion_graph"] as
    | Record<string, unknown>
    | undefined;
  const railPreset =
    motionGraph && typeof motionGraph["rail_location_name"] === "string"
      ? (motionGraph["rail_location_name"] as string)
      : null;

  // Prefer the edge-gated panel URL from the registry (pill.link_href, e.g.
  // "/xarm5/web/" behind Caddy forward_auth) over the device's raw
  // base_url/web, which is the directly-reachable Tailnet side-door.
  const controlPanelUrl =
    snapshot.pill?.link_href ??
    (snapshot.base_url
      ? `${snapshot.base_url.replace(/\/+$/, "")}/web`
      : null);

  return (
    <TileShell
      snapshot={snapshot}
      headerRight={
        <>
          <LockButton
            locked={locked}
            countdown={countdown}
            onToggle={toggle}
            noun="robot arm"
          />
          <StatusPill state={status.equipment_status} />
        </>
      }
    >
      <div className="flex flex-col gap-1.5">
        {/* ARM: state · TCP speed · angular speed */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="w-14 shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
            Arm
          </span>
          <Pill
            value={arm?.state ?? "—"}
            tone={componentTone(arm?.state)}
            title={arm?.message ?? undefined}
          />
          <Pill caption="TCP" value={fmt(tcpSpeed)} />
          <Pill caption="Ang" value={fmt(angleSpeed)} />
        </div>

        {/* GRIPPER: state · position · force */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="w-14 shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
            Gripper
          </span>
          <Pill
            value={gripper?.state ?? "—"}
            tone={componentTone(gripper?.state)}
            title={gripper?.message ?? undefined}
          />
          <Pill
            caption={gripperPos ? "Stroke" : "Range"}
            value={
              gripperPos
                ? fmt(gripperPos, 1)
                : strokeMin != null && strokeMax != null
                  ? `${strokeMin}–${strokeMax} mm`
                  : "—"
            }
            title={
              gripperPos
                ? "Current gripper stroke (opening width)"
                : "Configured stroke range; device does not publish current position yet"
            }
            tone={gripperPos ? "neutral" : "muted"}
          />
          <Pill
            caption="Force"
            value={
              ftEnabled && ftForce
                ? fmt(ftForce, 1)
                : configForce != null
                  ? `${configForce} cfg`
                  : "—"
            }
            tone={ftEnabled && ftForce ? "neutral" : "muted"}
            title={
              ftEnabled
                ? "Wrist force-torque sensor reading"
                : configForce != null
                  ? `Configured grip force (FT sensor disabled)`
                  : "No force reading available"
            }
          />
        </div>

        {/* TRACK: position · preset (only when parked at a named location) */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="w-14 shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
            Track
          </span>
          <Pill
            value={track?.state ?? "—"}
            tone={componentTone(track?.state)}
            title={track?.message ?? undefined}
          />
          <Pill caption="Pos" value={fmt(trackPos, 1)} />
          {railPreset ? (
            <Pill caption="At" value={railPreset} tone="ok" />
          ) : (
            <Pill
              caption="At"
              value="—"
              tone="muted"
              title="Track is between named rail locations"
            />
          )}
        </div>
      </div>

      {controlPanelUrl && (
        <div className="self-start">
          {locked ? (
            <button
              type="button"
              disabled
              title="Unlock to open the xArm control panel"
              className={[
                "inline-flex h-7 shrink-0 items-center justify-center gap-1 rounded-md border px-2.5 text-xs font-semibold",
                "cursor-not-allowed opacity-40",
                "border-slate-200 bg-white text-ink-muted",
                "dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300",
              ].join(" ")}
            >
              Open control panel ↗
            </button>
          ) : (
            <a
              href={controlPanelUrl}
              target="_blank"
              rel="noopener noreferrer"
              className={[
                "inline-flex h-7 shrink-0 items-center justify-center gap-1 rounded-md border px-2.5 text-xs font-semibold transition-colors",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500",
                "border-emerald-300 bg-emerald-50 text-emerald-800 hover:bg-emerald-100",
                "dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200 dark:hover:bg-emerald-900/60",
              ].join(" ")}
            >
              Open control panel ↗
            </a>
          )}
        </div>
      )}

      {snapshot.fetch_error && <FetchErrorBand error={snapshot.fetch_error} />}
    </TileShell>
  );
}
