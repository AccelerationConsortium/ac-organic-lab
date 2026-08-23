"use client";

import {
  useEffect,
  useState,
  type FormEvent,
} from "react";
import type { EquipmentSnapshot } from "@/types/api";
import {
  postSealerSealStart,
  postSealerSealStop,
  postSealerSetTemperature,
  postSealerSetTime,
  postSealerShutdown,
  postSealerStageIn,
  postSealerStageOut,
  postSealerStartup,
} from "@/lib/api";
import type { Parse412 } from "@/lib/action-error";
import { useActionError } from "@/lib/use-action-error";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { PositionPill, TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

// Validation ranges mirror skills/.../skill_catalog/plate_sealer.py.
const TEMP_MIN = 20;
const TEMP_MAX = 235;
const TIME_MIN = 0.5;
const TIME_MAX = 12.0;

// Heater state from device's components.heater (added by the device service;
// gracefully absent on older deployments).
type HeaterState =
  | "stable"
  | "heating"
  | "cooling"
  | "unknown"
  | "disconnected";

// Stage position from device's components.stage (plateloc v1.3+; "unknown"
// on older deployments or after process restart per the v1.3 contract).
type StageState = "in" | "out" | "unknown";

interface SealerState {
  actualTempC: number | null;
  setpointTempC: number | null;
  sealingTimeS: number | null;
  cycleCount: number | null;
  // Heater fields: null when the device hasn't published them yet (old service).
  heaterState: HeaterState | null;
  heaterMessage: string | null;
  // Plate-stage position (plateloc v1.3+); null when the device hasn't
  // published components.stage at all.
  stageState: StageState | null;
  // Half-width of the band; device authoritative. `null` when the device
  // doesn't publish details.temperature_tolerance_c — in that case the
  // tile skips its UX band check and relies on the device's 412 refusal
  // (layer 1) as the sole gate.
  toleranceC: number | null;
}

function parseSealer(snapshot: EquipmentSnapshot): SealerState {
  const metrics = snapshot.status.metrics ?? {};
  const components = snapshot.status.components ?? {};
  const details = (snapshot.status.details ?? {}) as Record<string, unknown>;
  const num = (key: string): number | null => {
    const v = metrics[key]?.value;
    return typeof v === "number" ? v : null;
  };
  const heater = components["heater"];
  const heaterState =
    heater && typeof heater.state === "string"
      ? (heater.state as HeaterState)
      : null;
  const stage = components["stage"];
  const rawStageState =
    stage && typeof stage.state === "string" ? stage.state : null;
  const stageState: StageState | null =
    rawStageState === "in" || rawStageState === "out" || rawStageState === "unknown"
      ? rawStageState
      : null;
  const rawTol = details["temperature_tolerance_c"];
  const toleranceC = typeof rawTol === "number" && rawTol > 0 ? rawTol : null;
  return {
    actualTempC: num("actual_temperature"),
    setpointTempC: num("setpoint_temperature"),
    sealingTimeS: num("sealing_time"),
    cycleCount: num("cycle_count"),
    heaterState,
    heaterMessage: heater?.message ?? null,
    stageState,
    toleranceC,
  };
}

// Map heater.state -> visual tone applied to the Actual pill and a
// short label appended to its caption ("Actual · heating", etc.).
type Tone = "neutral" | "ok" | "warn" | "muted";

const TONE_CLASSES: Record<Tone, string> = {
  neutral:
    "border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/40",
  ok: "border-emerald-300 bg-emerald-50 dark:border-emerald-700 dark:bg-emerald-950/40",
  warn: "border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/40",
  muted:
    "border-slate-200 bg-slate-100 dark:border-slate-700 dark:bg-slate-800/20",
};

function heaterTone(state: HeaterState | null): Tone {
  if (state === "stable") return "ok";
  if (state === "heating" || state === "cooling") return "warn";
  if (state === "unknown" || state === "disconnected") return "muted";
  return "neutral"; // heater field absent on the device
}

function fmt(value: number | null, unit: string, decimals: number): string {
  if (value === null || value === undefined) return `— ${unit}`;
  return `${value.toFixed(decimals)} ${unit}`;
}

/** Single-line read-only pill: caption left, value right. `tone` tints
 *  the border + background (used for heater state on the Actual pill);
 *  `title` becomes the native hover tooltip (heater.message). */
/**
 * plateloc's `seal.start` 412 has three distinct body shapes:
 *
 *   stage interlock (v1.3+):
 *     { detail, stage_state: "out" | "unknown", required: "in" }
 *
 *   health interlock (v1.4+ — refuses a run while `last_error` is uncleared):
 *     { detail, last_error_code, last_error_message, retry_after_s }
 *
 *   temperature interlock (v1.2+):
 *     { detail, actual_c, setpoint_c, tolerance_c, retry_after_s }
 *
 * We branch on which fields are present. Only `seal.start` carries these;
 * every other refusal (423 claim, 409 state) is handled generically by
 * interpretActionError. Module-scope so useActionError's identity is stable.
 * Exported for tests.
 */
export const parseSealer412: Parse412 = (body, { action, retryAfterS }) => {
  if (action !== "seal.start") return null;
  // Stage interlock first (operator-driven, fastest to fix).
  if (typeof body.stage_state === "string") {
    const stage = String(body.stage_state);
    const hint =
      stage === "out"
        ? ' Click "Stage in" first.'
        : stage === "unknown"
          ? ' Click "Stage in" or "Stage out" to home the stage.'
          : "";
    return `Plate stage is ${stage}, needs to be loaded.${hint}`;
  }
  // Health interlock (v1.4+): the run is refused while last_error is
  // uncleared. Reuse the per-code recovery copy from the last_error band so
  // the refusal and the band always say the same thing; the §6.4 window
  // also self-clears, hence the retry hint.
  if (typeof body.last_error_code === "string" || "last_error_message" in body) {
    const code =
      typeof body.last_error_code === "string" ? body.last_error_code : null;
    const raw =
      typeof body.last_error_message === "string" ? body.last_error_message : "";
    const recovery = recoveryForCode(code) ?? raw ?? "see the device log";
    const retry =
      typeof body.retry_after_s === "number" ? body.retry_after_s : retryAfterS;
    const retryStr =
      retry !== null && retry > 0
        ? ` Retrying in ~${Math.ceil(retry)} s also clears it.`
        : "";
    return `Clear the last fault first — ${recovery}${retryStr}`;
  }
  // Temperature interlock.
  const actual = typeof body.actual_c === "number" ? body.actual_c : null;
  const setpoint = typeof body.setpoint_c === "number" ? body.setpoint_c : null;
  const tol = typeof body.tolerance_c === "number" ? body.tolerance_c : null;
  const retry =
    typeof body.retry_after_s === "number" ? body.retry_after_s : retryAfterS;
  if (actual !== null && setpoint !== null && tol !== null) {
    const retryStr =
      retry !== null && retry > 0 ? ` Try again in ~${Math.ceil(retry)} s.` : "";
    return `Heater at ${actual.toFixed(0)} °C, need ${setpoint.toFixed(
      0,
    )} ±${tol} °C.${retryStr}`;
  }
  return null;
};

/**
 * What we render in the rose `last_error` band above the action buttons.
 * `code` is the v1.3.1+ taxonomy slug from the device (low_air_pressure,
 * com_init_failed, …). `recovery` is the dashboard's prescriptive copy.
 * `raw` is the device's verbatim message — shown truncated with a
 * hover tooltip so the operator can still see the driver-level detail.
 */
interface LastErrorBand {
  code: string | null;
  recovery: string;
  raw: string;
}

/**
 * plateloc's `last_error.code` taxonomy (v1.3.1+, extended v1.3.2 with
 * no_plate / vacuum_error) → one prescriptive recovery sentence per code.
 * Shared by the rose `last_error` band AND the v1.4+ health-interlock 412
 * (`parseSealer412`), so the refusal and the band always give the same
 * advice. Returns null for unknown / absent codes — the forward-compat
 * path for new device codes and the back-compat path for devices that
 * don't populate `code`.
 *
 * Reference: STATUS_SPEC §6 — last_error.code taxonomy is per-device;
 * each device's README documents its own codes. New codes need an entry
 * here when devices introduce them. Exported for tests.
 */
export function recoveryForCode(code: string | null): string | null {
  switch (code) {
    case "low_air_pressure":
      return "Air supply low. Check the regulator at ~80 psi.";
    case "vacuum_error":
      return "Vacuum fault — check the compressed-air supply and that the plate is seated flat.";
    case "no_plate":
      return "No plate detected on the stage. Load a plate (or re-seat it), then retry.";
    case "com_init_failed":
    case "com_timeout":
      return "Driver unresponsive — restart the device service.";
    case "profile_not_found":
      return "Open the Diagnostics dialog on the device PC and create the profile.";
    case "stage_jam":
      return "Stage move failed. Check the carriage path, then re-home with Stage in / Stage out.";
    case "heater_overtemp":
    case "heater_undertemp":
      return "Heater fault — service required.";
    case "process_internal":
      return "Lab-software bug — please file an issue.";
    case "com_other":
      return "Driver fault — see message.";
    default:
      return null;
  }
}

function interpretLastError(
  errorInfo: { code?: string | null; message?: string | null } | null | undefined,
): LastErrorBand | null {
  if (!errorInfo) return null;
  const raw = (errorInfo.message ?? "").trim();
  const code = errorInfo.code ?? null;
  const recovery = recoveryForCode(code);
  if (recovery === null) {
    // v1.0 device (no code), or a new code we don't map yet. Render the raw
    // message verbatim; suppress the band entirely if there's nothing to say.
    if (!raw) return null;
    return { code, recovery: "", raw };
  }
  return { code, recovery, raw };
}

function MetricPill({
  caption,
  value,
  tone = "neutral",
  title,
}: {
  caption: string;
  value: string;
  tone?: Tone;
  title?: string;
}) {
  return (
    <div
      className={`flex h-7 items-center gap-1 rounded-md border px-2 ${TONE_CLASSES[tone]}`}
      title={title}
    >
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
        {caption}
      </span>
      <span className="ml-auto text-xs font-semibold text-ink dark:text-slate-100 tabular-nums">
        {value}
      </span>
    </div>
  );
}

/** Single-line editable pill: caption + optional live reading + input +
 *  unit + Set, all inline. With `actual` set, one pill carries the live
 *  value and the setpoint together (e.g. "TEMP 165 °C [170] °C Set"). */
function EditablePill({
  caption,
  actual,
  tone = "neutral",
  title,
  current,
  unit,
  step,
  min,
  max,
  decimals,
  locked,
  busy,
  onSet,
}: {
  caption: string;
  actual?: string;
  tone?: Tone;
  title?: string;
  current: number | null;
  unit: string;
  step: number;
  min: number;
  max: number;
  decimals: number;
  locked: boolean;
  busy: boolean;
  onSet: (value: number) => void;
}) {
  const [draft, setDraft] = useState<string>(current?.toFixed(decimals) ?? "");

  // When the device-reported value changes (and the user isn't editing),
  // sync the draft.
  useEffect(() => {
    setDraft(current?.toFixed(decimals) ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (locked || busy) return;
    const parsed = parseFloat(draft);
    if (Number.isNaN(parsed)) return;
    if (parsed < min || parsed > max) return;
    onSet(parsed);
  }

  const disabled = locked || busy;
  return (
    <form
      onSubmit={handleSubmit}
      className={`flex h-7 items-center gap-1 rounded-md border px-2 ${TONE_CLASSES[tone]}`}
      title={title}
    >
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
        {caption}
      </span>
      {actual !== undefined && (
        <span className="shrink-0 text-xs font-semibold text-ink dark:text-slate-100 tabular-nums">
          {actual}
        </span>
      )}
      <input
        type="number"
        inputMode="decimal"
        step={step}
        min={min}
        max={max}
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        className="ml-auto w-12 min-w-0 rounded border border-slate-200 bg-white px-1 py-0 text-right text-xs tabular-nums text-ink outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-300 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
      />
      <span className="shrink-0 text-[10px] text-ink-subtle dark:text-slate-400">
        {unit}
      </span>
      <TileButton type="submit" size="small" variant="primary" disabled={disabled}>
        Set
      </TileButton>
    </form>
  );
}

export function PlateSealerTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const sealer = parseSealer(snapshot);
  const { locked, countdown, toggle } = useControlLock(snapshot.id);
  const { actionError, setActionError, exec } = useActionError(parseSealer412);

  const status = snapshot.status.equipment_status;
  const isBusy = status === "busy";
  const isReady = status === "ready";
  const isRequiresInit = status === "requires_init";

  // For inputs and setpoint actions we use the lock; busy doesn't disable
  // setpoint changes (the device accepts them between cycles, and the
  // server's claim semantics will reject if it doesn't).
  const controlsDisabled = locked;

  // Seal start interlock — UX safety net only. Authoritative enforcement
  // is layer-1 on the device (refuses with HTTP 412; see plateloc v1.2+).
  // We pre-block when we can compute the band ourselves so the operator
  // gets instant feedback instead of round-tripping a refusal. When the
  // tolerance / actual / setpoint aren't all known, we trust the device.
  //
  // Why not trust components.heater.state == "stable" alone: some firmware
  // reports "stable" as soon as the PID loop settles, even with a
  // steady-state offset (observed sealing at 150 °C against setpoint
  // 160 °C; device said stable, dashboard wrongly allowed it pre-1.2).
  const tempDelta =
    sealer.actualTempC !== null && sealer.setpointTempC !== null
      ? Math.abs(sealer.actualTempC - sealer.setpointTempC)
      : null;
  const tempInBand =
    sealer.toleranceC === null || tempDelta === null
      ? true
      : tempDelta <= sealer.toleranceC;
  const heaterPresent = sealer.heaterState !== null;
  const heaterStable = sealer.heaterState === "stable";
  const heaterOk = !heaterPresent || heaterStable;
  // Stage gate (plateloc v1.3+). When the device doesn't publish
  // components.stage at all (older firmware), we don't enforce it
  // client-side and let the device's own 412 be the gate.
  const stagePresent = sealer.stageState !== null;
  const stageReady = !stagePresent || sealer.stageState === "in";
  const sealStartBlocked = !stageReady || !tempInBand || !heaterOk;

  // Tooltip explains why. Order matches the device's 412 priority:
  // stage first (operator-driven, fastest to fix), then temperature,
  // then heater. Aligns with parseSealer412's branching on the 412
  // body shape.
  let sealStartTitle: string | undefined;
  if (!stageReady) {
    sealStartTitle =
      sealer.stageState === "out"
        ? `Plate stage is OUT — click "Stage in" first`
        : `Plate stage position unknown — click "Stage in" or "Stage out" to home`;
  } else if (!tempInBand) {
    sealStartTitle = `Heater not at setpoint: actual ${sealer.actualTempC?.toFixed(
      0,
    )} °C, setpoint ${sealer.setpointTempC?.toFixed(
      0,
    )} °C, tolerance ±${sealer.toleranceC} °C`;
  } else if (!heaterOk) {
    sealStartTitle = sealer.heaterMessage ?? "Heater not stable";
  }

  // exec (from useActionError) captures per-action errors so 412 / 423 / 409
  // surface in the shared <ActionErrorBand> rather than being swallowed. The
  // skill name (e.g. "seal.start") is threaded through so parseSealer412 can
  // specialise on the 412 body shape from /control/seal/start (plateloc v1.2+).

  // Auto-clear when the device transitions to a state where the previous
  // error is no longer relevant: ready + nothing else blocking seal.start.
  useEffect(() => {
    if (actionError && isReady && !sealStartBlocked) {
      setActionError(null);
    }
  }, [actionError, isReady, sealStartBlocked]);

  // The last_error fault surface is standardized in TileShell as a message
  // icon + popover next to the status pill; we pass interpretLastError to it
  // (below) so plateloc's v1.3.1 code taxonomy gets prescriptive recovery copy.

  // Footer message override: when the device is in `ready` but the seal
  // interlock would refuse, the device's own `status.message` (typically
  // "Idle, ready to seal") is misleading — it's the device's view of
  // its own state machine, not the dashboard's. Surface the actual
  // blocker so the operator doesn't see a green "ready to seal" message
  // alongside a greyed-out Seal start button. Matches TileShell's
  // default truncate + title rendering so the full text is recoverable
  // on hover.
  const footerOverride =
    isReady && sealStartBlocked && sealStartTitle ? (
      <div className="truncate" title={sealStartTitle}>
        {sealStartTitle}
      </div>
    ) : undefined;

  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      footerLeft={footerOverride}
      lifecycle={{
        // ON toggles startup/shutdown; STOP is the halt (seal.stop aborts the
        // seal cycle — always available, never a disconnect).
        isOn: !isRequiresInit,
        initLabel: "INIT",
        onPowerToggle: () =>
          isRequiresInit
            ? exec(() => postSealerStartup(snapshot.id), { action: "startup" })
            : exec(() => postSealerShutdown(snapshot.id), {
                action: "shutdown",
              }),
        onStop: () =>
          exec(() => postSealerSealStop(snapshot.id), { action: "seal.stop" }),
        disabled: controlsDisabled,
        powerTitle: isRequiresInit
          ? "Device is off — click to start up"
          : "Device is on — click to shut down",
        stopTitle: "Halt: abort the seal cycle",
      }}
      headerRight={
        <>
          <LockButton
            locked={locked}
            countdown={countdown}
            onToggle={toggle}
            noun="sealer"
          />
          <StatusPill state={status} />
        </>
      }
      lastErrorInterpret={interpretLastError}
    >
      {/* One metric row: Temp (live actual + editable setpoint in a single
          pill, tinted by heater state — out-of-band always trumps so the pill
          never looks "ready" while the seal interlock blocks), Seal time,
          Cycles. */}
      <div className="flex flex-wrap items-center gap-1.5">
        <EditablePill
          caption="Temp"
          actual={fmt(sealer.actualTempC, "°C", 0)}
          tone={!tempInBand ? "warn" : heaterTone(sealer.heaterState)}
          title={sealStartTitle ?? sealer.heaterMessage ?? undefined}
          current={sealer.setpointTempC}
          unit="°C"
          step={1}
          min={TEMP_MIN}
          max={TEMP_MAX}
          decimals={0}
          locked={locked}
          busy={isBusy}
          onSet={(v) =>
            exec(() => postSealerSetTemperature(snapshot.id, v), {
              action: "seal.set_temperature",
            })
          }
        />
        <EditablePill
          caption="Seal time"
          current={sealer.sealingTimeS}
          unit="s"
          step={0.1}
          min={TIME_MIN}
          max={TIME_MAX}
          decimals={1}
          locked={locked}
          busy={isBusy}
          onSet={(v) =>
            exec(() => postSealerSetTime(snapshot.id, v), {
              action: "seal.set_time",
            })
          }
        />
        <MetricPill
          caption="Cycles"
          value={
            sealer.cycleCount !== null
              ? sealer.cycleCount.toLocaleString()
              : "—"
          }
        />
      </div>

      {/* Device fault (last_error) is surfaced as a message icon next to the
          status pill in the header (see headerRight) — click to pop the
          detail box open/closed — rather than an always-expanded band here. */}

      {/* Action buttons (lifecycle ON/STOP live in the template banner). */}
      <div className="flex flex-wrap items-center gap-1">
        {isReady && (
          <>
            {/* Stage in / Stage out are click-to-move pills, same pattern
                as PressTile's plate IN/OUT. The pill matching the current
                stage.state is highlighted emerald; click the other one to
                move there. When the device hasn't published
                components.stage.state (older firmware), neither is
                highlighted and both stay clickable — the device's own
                412 enforces the precondition. */}
            <PositionPill
              label="Stage in"
              isCurrent={sealer.stageState === "in"}
              isMoving={isBusy && sealer.stageState !== "in"}
              disabled={controlsDisabled || sealer.stageState === "in"}
              onClick={() =>
                exec(() => postSealerStageIn(snapshot.id), { action: "stage.in" })
              }
            />
            <PositionPill
              label="Stage out"
              isCurrent={sealer.stageState === "out"}
              isMoving={isBusy && sealer.stageState !== "out"}
              disabled={controlsDisabled || sealer.stageState === "out"}
              onClick={() =>
                exec(() => postSealerStageOut(snapshot.id), {
                  action: "stage.out",
                })
              }
            />
            <TileButton
              onClick={() =>
                exec(() => postSealerSealStart(snapshot.id), {
                  action: "seal.start",
                })
              }
              disabled={controlsDisabled || sealStartBlocked}
              variant="primary"
              title={sealStartTitle}
            >
              Seal start
            </TileButton>
          </>
        )}
      </div>

      {/* Inline error band: 412 / 423 / 409 from the last action.
          Auto-clears on next click or when the device transitions to
          ready + tempInBand + heaterOk. */}
    </TileShell>
  );
}
