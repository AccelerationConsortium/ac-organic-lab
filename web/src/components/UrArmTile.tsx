"use client";

import { useState, useTransition } from "react";

import type { EquipmentSnapshot } from "@/types/api";
import { postArmConnect, postArmDisconnect, postArmStop } from "@/lib/api";
import { devicePanelPath } from "@/lib/device-panels";
import { useActionError } from "@/lib/use-action-error";
import { useControlLock } from "@/lib/use-control-lock";
import { AuthGatedLink } from "./AuthGatedLink";
import { LockButton } from "./ControlLock";
import { FetchErrorBand } from "./FetchErrorBand";
import { StatusPill } from "./StatusPill";
import { TileButton } from "./TileButton";
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
        <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
          {caption}
        </span>
      ) : null}
      <span className="text-xs font-semibold text-ink dark:text-slate-100 tabular-nums">
        {value}
      </span>
    </div>
  );
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function controllerTone(mode: string | null): Tone {
  if (!mode) return "muted";
  if (mode === "running") return "ok";
  if (mode === "power_off" || mode === "idle" || mode === "booting") return "muted";
  return "warn";
}

function safetyTone(state: string | null): Tone {
  if (!state) return "muted";
  if (state === "normal") return "ok";
  return "warn";
}

function gripperTone(state: string | null): Tone {
  if (!state) return "muted";
  if (state === "enabled") return "ok";
  if (state === "error" || state === "fault") return "warn";
  return "muted";
}

/**
 * The Ligand Development UR5e (robot-motion on sdl2-pc-05). The device is a
 * STATUS_SPEC robot_arm whose *observation* (RTDE receive) is always on: its
 * equipment_status reads `ready` whenever the controller is up, so unlike the
 * xArm's RobotArmTile, "connected" here is the device's **arm session**
 * (`details.control_session.open`), which POST /connect opens and
 * /disconnect closes. Moves and the gripper then need the claim, which lives
 * in the device panel; this tile offers the claim-free lifecycle and the
 * safety floor, as the xArm tile does:
 *
 *   - INIT / ON toggles the arm session (connect needs Remote Control on the
 *     pendant and no claim held by someone else; the device folds both into
 *     `allowed_actions`, which the button follows);
 *   - STOP is identity-only on the device and stops the arm, a trajectory
 *     and the gripper. Nothing moves without a session, except the gripper
 *     (its own socket), so STOP is offered when either is live;
 *   - Clear errors is holder-only on this device (the stop latch belongs to
 *     the claim), so there is no CLEAR button: the panel has it.
 */
export function UrArmTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const { status, fetch_error: fetchError } = snapshot;
  const reachable = !fetchError;
  const details = reachable ? record(status.details) ?? {} : {};
  const components = reachable ? status.components ?? {} : {};
  const allowed = new Set(reachable ? status.allowed_actions ?? [] : []);
  const { locked, noAccess, toggle, countdown } = useControlLock(snapshot.id);

  const { actionError, setActionError, reportError } = useActionError();
  const [pending, setPending] = useState(false);
  const [, startTransition] = useTransition();
  const [confirmOff, setConfirmOff] = useState(false);

  const session = record(details["control_session"]);
  const sessionOpen = session?.["open"] === true;
  const sessionBusy = session?.["busy"] === true;
  const manualMode = session?.["manual_mode"] === true;
  const latched = text(session?.["latched"]);
  const claimedBy = record(details["claimed_by"]);
  const holder = text(claimedBy?.["owner"]);
  const gripper = record(details["gripper"]);
  const gripperBusy = gripper?.["busy"] === true;
  const gripperState = text(gripper?.["state"]) ?? text(components["gripper"]?.state);
  const opening = gripper?.["opening_mm"];
  const openingMm = typeof opening === "number" ? opening : null;
  const telemetry = record(details["telemetry"]);
  const force = record(telemetry?.["tcp_force"]);
  const forceN = force?.["force_magnitude_n"];
  const forceMagnitude = typeof forceN === "number" ? forceN : null;

  const mode = text(details["robotmode"]) ?? text(components["controller"]?.state);
  const modeLower = mode ? mode.toLowerCase() : null;
  const safety = text(details["safetystatus"]) ?? text(components["safety"]?.state);
  const safetyLower = safety ? safety.toLowerCase() : null;
  const remote = details["remote_control"];
  const remoteControl = typeof remote === "boolean" ? remote : null;

  function runControl(name: string, fn: () => Promise<unknown>) {
    setActionError(null);
    setPending(true);
    startTransition(() => {
      fn()
        .catch((e: unknown) => reportError(e, name))
        .finally(() => setPending(false));
    });
  }

  function onToggle() {
    if (sessionOpen) setConfirmOff(true);
    else runControl("connect", () => postArmConnect(snapshot.id));
  }

  function onConfirmDisconnect() {
    setConfirmOff(false);
    runControl("disconnect", () => postArmDisconnect(snapshot.id));
  }

  const ctrlBlocked = locked || pending || !reachable;
  const lockTitle = noAccess ? "No access to this equipment" : "Sign in to control";

  // The device advertises connect / disconnect only when it would accept
  // them: Remote Control on, nobody else holding control, arm still.
  const canConnect = !sessionOpen && allowed.has("connect");
  const canDisconnect = sessionOpen && allowed.has("disconnect") && !sessionBusy;
  const toggleDisabled = ctrlBlocked || (sessionOpen ? !canDisconnect : !canConnect);
  const toggleTitle = locked
    ? lockTitle
    : sessionOpen
      ? sessionBusy
        ? "Arm is moving — Stop before disconnecting"
        : canDisconnect
          ? "Arm session open — click to disconnect"
          : holder
            ? `Control held by ${holder}; only they can disconnect`
            : "Disconnect is not offered by the device right now"
      : canConnect
        ? "Arm session closed — click to connect"
        : remoteControl === false
          ? "Switch the pendant to Remote Control first"
          : holder
            ? `Control held by ${holder}`
            : "Connect is not offered by the device right now (see the panel)";

  // Nothing to stop before Connect, unless the gripper is moving.
  const stoppable = sessionOpen || gripperBusy;
  const stopTitle = locked
    ? lockTitle
    : stoppable
      ? "Stop the arm (and the gripper); moves stay latched until Clear errors in the panel"
      : "Nothing to stop: the arm session is closed";

  const controlPanelUrl =
    snapshot.pill?.link_href ?? devicePanelPath(snapshot.id);

  const panelLink = !controlPanelUrl ? null : (
    <AuthGatedLink
      href={controlPanelUrl}
      equipmentId={snapshot.id}
      external
      title={locked ? `${lockTitle} to open the ${snapshot.name} control panel` : undefined}
      className={[
        "inline-flex h-7 shrink-0 items-center justify-center gap-1 rounded-md border px-2.5 text-xs font-semibold transition-colors",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orange-500",
        "border-orange-300 bg-orange-50 text-orange-800 hover:bg-orange-100",
        "dark:border-orange-700 dark:bg-orange-950/40 dark:text-orange-200 dark:hover:bg-orange-900/60",
      ].join(" ")}
    >
      Open control panel ↗
    </AuthGatedLink>
  );

  const sessionValue = !reachable
    ? "unknown"
    : sessionOpen
      ? manualMode
        ? "open · manual"
        : sessionBusy
          ? "open · moving"
          : "open"
      : "closed";

  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      footerLeft={fetchError ? "Service unreachable; current arm state is unknown." : undefined}
      bannerExtra={
        <>
          <div className="relative">
            <TileButton
              variant="primary"
              disabled={toggleDisabled}
              onClick={onToggle}
              title={toggleTitle}
            >
              <span
                className={[
                  "mr-1 inline-block h-2 w-2 rounded-full",
                  sessionOpen
                    ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.7)]"
                    : "bg-slate-400",
                ].join(" ")}
                aria-hidden
              />
              {sessionOpen ? "ON" : "INIT"}
            </TileButton>
            {confirmOff && (
              <div className="absolute left-0 top-full z-20 mt-1 w-44 rounded-md border border-slate-200 bg-white p-2 shadow-lg dark:border-slate-700 dark:bg-slate-900">
                <p className="mb-2 text-xs text-ink dark:text-slate-200">
                  Close the arm session?
                </p>
                <div className="flex justify-end gap-1.5">
                  <TileButton size="small" onClick={() => setConfirmOff(false)}>
                    Cancel
                  </TileButton>
                  <TileButton
                    size="small"
                    variant="danger"
                    onClick={onConfirmDisconnect}
                  >
                    Yes
                  </TileButton>
                </div>
              </div>
            )}
          </div>
          <TileButton
            variant="danger"
            disabled={ctrlBlocked || !stoppable}
            onClick={() => runControl("move/stop", () => postArmStop(snapshot.id))}
            title={stopTitle}
          >
            STOP
          </TileButton>
          {panelLink && <div className="ml-auto">{panelLink}</div>}
        </>
      }
      headerRight={
        <>
          <LockButton
            locked={locked}
            countdown={countdown}
            onToggle={toggle}
            noun="robot arm"
          />
          <StatusPill state={fetchError ? "unknown" : status.equipment_status} />
        </>
      }
    >
      <div className="flex flex-col gap-1.5">
        {/* ARM: controller mode · session · who holds control */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="w-14 shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
            Arm
          </span>
          <Pill
            value={modeLower ?? "unknown"}
            tone={controllerTone(modeLower)}
            title={
              remoteControl === null
                ? "Controller mode"
                : `Controller mode · pendant in ${remoteControl ? "Remote" : "Local"} control`
            }
          />
          <Pill
            caption="Session"
            value={sessionValue}
            tone={!reachable ? "muted" : sessionOpen ? "ok" : "muted"}
            title={
              latched
                ? `Moves latched: ${latched} (Clear errors in the panel)`
                : "The device's arm control session; Connect opens it"
            }
          />
          {latched && <Pill value="latched" tone="warn" title={latched} />}
          <Pill
            caption="Control"
            value={holder ?? "nobody"}
            tone={holder ? "neutral" : "muted"}
            title={holder ? "Claim holder (device panel)" : "No claim held; take control in the panel"}
          />
        </div>

        {/* SAFETY: controller safety status · pendant mode */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="w-14 shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
            Safety
          </span>
          <Pill value={safetyLower ?? "unknown"} tone={safetyTone(safetyLower)} />
          {remoteControl !== null && (
            <Pill
              caption="Pendant"
              value={remoteControl ? "remote" : "local"}
              tone={remoteControl ? "ok" : "warn"}
              title={
                remoteControl
                  ? "Remote Control on: the service may open the arm session"
                  : "Local control: switch the pendant to Remote before Connect"
              }
            />
          )}
          {forceMagnitude !== null && (
            <Pill
              caption="Force"
              value={`${forceMagnitude.toFixed(1)} N`}
              tone={forceMagnitude > 30 ? "warn" : "neutral"}
              title="TCP force magnitude (zero it in the panel when the arm is still)"
            />
          )}
        </div>

        {/* GRIPPER: state · opening */}
        {(gripper || components["gripper"]) && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-14 shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
              Gripper
            </span>
            <Pill
              value={gripperBusy ? `${gripperState ?? "enabled"} · moving` : gripperState ?? "unknown"}
              tone={gripperTone(gripperState)}
              title={text(gripper?.["name"]) ?? undefined}
            />
            {openingMm !== null && (
              <Pill
                caption="Opening"
                value={`${openingMm.toFixed(1)} mm`}
                title="Finger opening, from the gripper's position register"
              />
            )}
          </div>
        )}
      </div>

      {fetchError && <FetchErrorBand error={fetchError} />}
    </TileShell>
  );
}
