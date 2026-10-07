"use client";

import { useRef, useState } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import {
  getEquipmentStatus,
  postPlateWeigherAction,
  type PlateWeigherAction,
} from "@/lib/api";
import type { Parse412 } from "@/lib/action-error";
import { claimHolder, claimLabel, claimTitle } from "@/lib/claim";
import {
  formatMassG,
  parsePlateWeigher,
  stabilityLabel,
} from "@/lib/plate-weigher";
import { useActionError } from "@/lib/use-action-error";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { PositionPill, TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

/**
 * Plate weigher (weigh-every-plate): balance + plate lift + optional lid.
 *
 * The balance row is the OT-2 gateway's slot-9 `platebalance` panel
 * (opentrons-server `ui/src/components/PlateBalanceControls.tsx`) carried
 * over: the fixed-width monospace mass box with its unit, the
 * Stable / Unstable / No-reading word beside it, a "wait until stable"
 * switch that defaults on, and Weight / Tare buttons gated on the device's
 * `allowed_actions`. What that panel does not have — because the OT-2's
 * balance has no lift — is the second row: the plate's two poses and the lid
 * as position pills, plus Park.
 *
 * Every verb is claim-gated on the device; the dashboard's passthrough takes
 * the per-request claim, so a claim visible on the poll is one we cannot get
 * and the controls are disabled with the holder named in the footer.
 */

const STABLE_READ_MAX_S = 10;

/** The weigher's 412 body: `detail` plus, for interlocks, the verb that
 *  would clear it (`required: "open_lid"`). */
const parseWeigher412: Parse412 = (body) => {
  const required = body.required;
  const detail = typeof body.detail === "string" ? body.detail : null;
  if (typeof required === "string" && required) {
    const verb = REQUIRED_LABELS[required] ?? required;
    return detail ? `${detail} Do "${verb}" first.` : `Blocked until "${verb}" runs.`;
  }
  return null;
};

const REQUIRED_LABELS: Readonly<Record<string, string>> = Object.freeze({
  startup: "INIT",
  open_lid: "Open lid",
  close_lid: "Close lid",
  lower: "Weighing",
  raise: "Raised",
});

function pendingCopy(action: PlateWeigherAction, waitStable: boolean): string {
  switch (action) {
    case "read":
      return waitStable
        ? `Weighing — waiting for a stable reading (up to ${STABLE_READ_MAX_S} s). Keep the pan still.`
        : "Weighing (immediate reading)…";
    case "tare":
      return "Taring…";
    case "zero":
      return "Zeroing…";
    case "startup":
      return "Connecting the balance and syncing the lift…";
    case "shutdown":
      return "Releasing the lift and closing the balance port…";
    case "raise":
      return "Raising the plate…";
    case "lower":
      return "Lowering the plate onto the pan…";
    case "open_lid":
      return "Opening the lid…";
    case "close_lid":
      return "Closing the lid…";
    case "park":
      return "Parking…";
    case "stop":
      return "Stopping…";
    default:
      return "Working…";
  }
}

const newer = (a: EquipmentSnapshot, b: EquipmentSnapshot) =>
  Date.parse(a.fetched_at ?? "") > Date.parse(b.fetched_at ?? "");

export function PlateWeigherTile({ snapshot: polled }: { snapshot: EquipmentSnapshot }) {
  // The list poll lags an action by a few seconds; after each verb the tile
  // reads the device once and shows that until the poll catches up.
  const [live, setLive] = useState<EquipmentSnapshot | null>(null);
  const snapshot = live && live.id === polled.id && newer(live, polled) ? live : polled;
  const weigher = parsePlateWeigher(snapshot);
  const { locked, countdown, toggle } = useControlLock(snapshot.id);
  const { actionError, reportError, clearError } = useActionError(parseWeigher412);
  const [waitStable, setWaitStable] = useState(true);
  const [pending, setPending] = useState<PlateWeigherAction | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const inFlight = useRef(false);

  const status = snapshot.status.equipment_status;
  const allowed = snapshot.status.allowed_actions ?? [];
  const claim = claimHolder(snapshot.status);
  const requiresInit = status === "requires_init";
  const isOn = !requiresInit && status !== "unknown";

  // The device is the authority: its allowed_actions and its 412 refusals
  // come from one precondition function, so mirroring the list can never
  // disagree with what a click would get.
  const advertised = (action: PlateWeigherAction) => allowed.includes(action);
  const can = (action: PlateWeigherAction) =>
    !locked && claim === null && !snapshot.fetch_error && advertised(action);
  const disabled = (action: PlateWeigherAction) =>
    !can(action) || (pending !== null && action !== "stop");

  async function send(action: PlateWeigherAction, body: Record<string, unknown> = {}) {
    if (!can(action) || inFlight.current) return;
    inFlight.current = true;
    setPending(action);
    clearError();
    setNotice(null);
    try {
      const ack = await postPlateWeigherAction(snapshot.id, action, body);
      if (action === "read" && typeof ack.mass_g === "number") {
        setNotice(`${ack.mass_g.toFixed(4)} g · ${ack.stable ? "stable" : "unstable"}`);
      } else if (action === "read" && ack.condition === "underload") {
        setNotice("Balance reads Low — nothing (or too little) on the pan.");
      } else if (action === "read" && ack.condition === "overload") {
        setNotice("Balance reads High — over capacity.");
      } else if (ack.message) {
        setNotice(ack.message);
      }
    } catch (error) {
      reportError(error, action);
    } finally {
      inFlight.current = false;
      setPending(null);
      try {
        setLive(await getEquipmentStatus(snapshot.id));
      } catch {
        /* the next poll will catch up */
      }
    }
  }

  const plateAt = (pose: "raised" | "weighing") =>
    weigher.liftPose === pose;
  const lidAt = (state: "open" | "closed") => weigher.lidState === state;

  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      lifecycle={{
        isOn,
        initLabel: "INIT",
        onPowerToggle: () => void send(isOn ? "shutdown" : "startup"),
        onStop: advertised("stop") ? () => void send("stop") : undefined,
        disabled: locked || claim !== null || pending !== null ||
          !(isOn ? advertised("shutdown") : advertised("startup")),
        powerTitle: isOn
          ? "Release the lift servos and close the balance port."
          : "Connect the balance and sync the lift: lid opens, plate lowers onto the pan. Hands clear.",
        confirmOff: "Release the lift and disconnect the balance?",
        stopTitle: "Halt the lift where it is.",
      }}
      headerRight={
        <LockButton locked={locked} countdown={countdown} onToggle={toggle} noun="weigher" />
      }
      footerLeft={
        claim
          ? <span title={claimTitle(claim)}>{claimLabel(claim)}</span>
          : pending
            ? <span role="status">{pendingCopy(pending, waitStable)}</span>
            : notice ?? undefined
      }
    >
      <div className="flex flex-col gap-1.5 text-xs">
        {/* Balance row — the OT-2 platebalance panel's readout and verbs. */}
        <div className="flex flex-wrap items-center gap-2">
          <div
            aria-label="Last measured weight"
            className="inline-flex items-baseline gap-2 rounded border border-slate-300 bg-slate-50 px-3 py-1.5 font-mono text-sm tabular-nums dark:border-slate-600 dark:bg-slate-900"
          >
            <span className="inline-block w-[9ch] whitespace-pre text-right">{formatMassG(weigher)}</span>
            <span>g</span>
          </div>
          <span className="text-ink-subtle dark:text-slate-400" title={weigher.balanceMessage ?? undefined}>
            {weigher.balanceConnected ? stabilityLabel(weigher) : "Balance not connected"}
          </span>
          <div className="ml-auto flex gap-1.5">
            <TileButton
              disabled={disabled("read")}
              onClick={() => void send("read", { stable: waitStable })}
              title={waitStable ? `Read once the balance reports stable (up to ${STABLE_READ_MAX_S} s).` : "Read immediately, stable or not."}
            >
              Weight
            </TileButton>
            <TileButton
              disabled={disabled("tare")}
              onClick={() => void send("tare")}
              title="Tare the balance. Refused while the lift is moving."
            >
              Tare
            </TileButton>
            {advertised("zero") && (
              <TileButton disabled={disabled("zero")} onClick={() => void send("zero")}>
                Zero
              </TileButton>
            )}
          </div>
        </div>
        <label className="flex items-center gap-2 text-ink-subtle dark:text-slate-400">
          <input
            type="checkbox"
            checked={waitStable}
            disabled={locked || pending !== null}
            onChange={(event) => setWaitStable(event.target.checked)}
          />
          Weight read: wait until stable · {STABLE_READ_MAX_S} s max
        </label>

        {/* Lift row — plate pose, lid, park. Not on the OT-2 panel: its
            balance has no lift. */}
        <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5 dark:border-slate-700 dark:bg-slate-800/40">
          <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
            Plate
          </span>
          <PositionPill
            label="Raised"
            isCurrent={plateAt("raised")}
            isMoving={pending === "raise"}
            disabled={disabled("raise")}
            onClick={() => void send("raise")}
            ariaLabel="Raise the plate off the pan"
          />
          <PositionPill
            label="Weighing"
            isCurrent={plateAt("weighing")}
            isMoving={pending === "lower"}
            disabled={disabled("lower")}
            onClick={() => void send("lower")}
            ariaLabel="Lower the plate onto the pan"
          />
          {weigher.lidFitted && (
            <>
              <span className="ml-1 shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
                Lid
              </span>
              <PositionPill
                label="Open"
                isCurrent={lidAt("open")}
                isMoving={pending === "open_lid"}
                disabled={disabled("open_lid")}
                onClick={() => void send("open_lid")}
                ariaLabel="Open the lid"
              />
              <PositionPill
                label="Closed"
                isCurrent={lidAt("closed")}
                isMoving={pending === "close_lid"}
                disabled={disabled("close_lid")}
                onClick={() => void send("close_lid")}
                ariaLabel="Close the lid"
              />
            </>
          )}
          <TileButton
            disabled={disabled("park")}
            onClick={() => void send("park")}
            title="Rest posture: plate down, lid closed (servo units) or retracted and de-energized (stepper units)."
          >
            Park
          </TileButton>
        </div>
      </div>
    </TileShell>
  );
}
