"use client";

import {
  useEffect,
  useState,
  useTransition,
  type FormEvent,
} from "react";
import type { EquipmentSnapshot } from "@/types/api";
import {
  ApiError,
  postSealerSealStart,
  postSealerSealStop,
  postSealerSetTemperature,
  postSealerSetTime,
  postSealerShutdown,
  postSealerStageIn,
  postSealerStageOut,
  postSealerStartup,
} from "@/lib/api";
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
 * What we render in the inline error band below the action buttons.
 * `kind` is a hint for future styling; today every kind shares the
 * amber tone (these are refusals to act, not catastrophic errors).
 */
interface ActionError {
  status: number;
  message: string;
  kind: "precondition" | "claim" | "state" | "other";
}

/** Translate a thrown error from a tile action into a renderable message. */
function interpretActionError(e: unknown, action: string): ActionError {
  if (!(e instanceof ApiError)) {
    const message = e instanceof Error ? e.message : String(e);
    return { status: 0, message, kind: "other" };
  }
  const body = (e.body ?? {}) as Record<string, unknown>;
  const detail = typeof body.detail === "string" ? body.detail : undefined;

  // 412 Precondition Failed - two distinct body shapes from plateloc:
  //
  //   stage interlock (v1.3+):
  //     { detail, stage_state: "out" | "unknown", required: "in" }
  //
  //   temperature interlock (v1.2+):
  //     { detail, actual_c, setpoint_c, tolerance_c, retry_after_s }
  //
  // We branch on which fields are present.
  if (e.status === 412) {
    if (action === "seal.start") {
      // Stage interlock first (operator-driven, fastest to fix).
      if (typeof body.stage_state === "string") {
        const stage = String(body.stage_state);
        const hint =
          stage === "out"
            ? ' Click "Stage in" first.'
            : stage === "unknown"
              ? ' Click "Stage in" or "Stage out" to home the stage.'
              : "";
        return {
          status: 412,
          message: `Plate stage is ${stage}, needs to be loaded.${hint}`,
          kind: "precondition",
        };
      }
      // Temperature interlock.
      const actual = typeof body.actual_c === "number" ? body.actual_c : null;
      const setpoint =
        typeof body.setpoint_c === "number" ? body.setpoint_c : null;
      const tol = typeof body.tolerance_c === "number" ? body.tolerance_c : null;
      const retry =
        typeof body.retry_after_s === "number"
          ? body.retry_after_s
          : e.retryAfterS;
      if (actual !== null && setpoint !== null && tol !== null) {
        const retryStr =
          retry !== null && retry > 0
            ? ` Try again in ~${Math.ceil(retry)} s.`
            : "";
        return {
          status: 412,
          message: `Heater at ${actual.toFixed(0)} °C, need ${setpoint.toFixed(
            0,
          )} ±${tol} °C.${retryStr}`,
          kind: "precondition",
        };
      }
    }
    return {
      status: 412,
      message: detail ?? "Device precondition not met.",
      kind: "precondition",
    };
  }

  // 423 Locked - another holder owns the claim.
  if (e.status === 423) {
    const claimedBy = body.claimed_by as { owner?: string } | undefined;
    const owner = claimedBy?.owner;
    return {
      status: 423,
      message: owner
        ? `Device claim is held by ${owner}. Try again later.`
        : detail ?? "Device is busy with another caller.",
      kind: "claim",
    };
  }

  // 409 Conflict - typically "driver not connected; click Startup first".
  if (e.status === 409) {
    const msg = detail ?? "Action rejected.";
    const hint = /init|startup|connect/i.test(msg) ? " Click Startup first." : "";
    return { status: 409, message: msg + hint, kind: "state" };
  }

  // Fall-through: surface whatever the device sent.
  return {
    status: e.status,
    message: detail ?? e.message,
    kind: "other",
  };
}

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
 * Branch on plateloc v1.3.1's `last_error.code` taxonomy. The mapping is
 * intentionally narrow: each code → one prescriptive recovery sentence.
 * Unknown / null codes fall back to a generic "see message" — that's the
 * forward-compat path for new device codes and the back-compat path for
 * v1.0 / pre-v1.3.1 devices that don't populate `code`.
 *
 * Reference: STATUS_SPEC §6 — last_error.code taxonomy is per-device;
 * each device's README documents its own codes. New codes need a branch
 * here when devices introduce them.
 */
function interpretLastError(
  errorInfo: { code?: string | null; message?: string | null } | null | undefined,
): LastErrorBand | null {
  if (!errorInfo) return null;
  const raw = (errorInfo.message ?? "").trim();
  const code = errorInfo.code ?? null;
  let recovery: string;
  switch (code) {
    case "low_air_pressure":
      recovery = "Air supply low. Check the regulator at ~80 psi.";
      break;
    case "com_init_failed":
    case "com_timeout":
      recovery = "Driver unresponsive — restart the device service.";
      break;
    case "profile_not_found":
      recovery = "Open the Diagnostics dialog on the device PC and create the profile.";
      break;
    case "stage_jam":
      recovery =
        "Stage move failed. Check the carriage path, then re-home with Stage in / Stage out.";
      break;
    case "heater_overtemp":
    case "heater_undertemp":
      recovery = "Heater fault — service required.";
      break;
    case "process_internal":
      recovery = "Lab-software bug — please file an issue.";
      break;
    case "com_other":
      recovery = "Driver fault — see message.";
      break;
    default:
      // v1.0 device (no code), or a new v1.3+ code we don't branch on yet.
      // Render the raw message verbatim; suppress the band entirely if
      // there's nothing to say.
      if (!raw) return null;
      recovery = "";
      break;
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
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
        {caption}
      </span>
      <span className="ml-auto font-mono text-xs font-semibold text-ink dark:text-slate-100 tabular-nums">
        {value}
      </span>
    </div>
  );
}

/** Single-line editable pill: caption + input + unit + Set, all inline. */
function EditablePill({
  caption,
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
      className="flex h-7 items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 dark:border-slate-700 dark:bg-slate-800/40"
    >
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
        {caption}
      </span>
      <input
        type="number"
        inputMode="decimal"
        step={step}
        min={min}
        max={max}
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        className="ml-auto w-12 min-w-0 rounded border border-slate-200 bg-white px-1 py-0 text-right font-mono text-xs tabular-nums text-ink outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-300 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
      />
      <span className="shrink-0 text-[10px] text-ink-subtle dark:text-slate-500">
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
  const [, startTransition] = useTransition();
  const { locked, countdown, toggle } = useControlLock(snapshot.id);
  const [actionError, setActionError] = useState<ActionError | null>(null);
  // The last_error fault band is collapsed to a small bubble by default (the
  // driver messages can be long); the operator clicks to expand / hide.
  const [faultExpanded, setFaultExpanded] = useState(false);

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
  // then heater. Aligns with PlateSealerTile.interpretActionError's
  // branching on the 412 body shape.
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

  // Capture per-action errors so 412 / 423 / 409 surface inline rather
  // than being swallowed. `actionName` is the skill name (e.g.
  // "seal.start") so interpretActionError can specialise on the 412
  // body shape that ships from /control/seal/start on plateloc v1.2+.
  function exec<T>(actionName: string, fn: () => Promise<T>) {
    // Optimistic clear: any new click supersedes a prior error.
    setActionError(null);
    startTransition(() => {
      fn().catch((err: unknown) => {
        setActionError(interpretActionError(err, actionName));
      });
    });
  }

  // Auto-clear when the device transitions to a state where the previous
  // error is no longer relevant: ready + nothing else blocking seal.start.
  useEffect(() => {
    if (actionError && isReady && !sealStartBlocked) {
      setActionError(null);
    }
  }, [actionError, isReady, sealStartBlocked]);

  // last_error band: plateloc v1.3.1+ ships a structured `last_error.code`
  // taxonomy and auto-clears the field on the next successful operational
  // action. So when this returns non-null, it's freshly meaningful —
  // not stale debris from a previous shift.
  const lastErrorBand = interpretLastError(snapshot.status.last_error);

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
      footerLeft={footerOverride}
      headerRight={
        <>
          <LockButton
            locked={locked}
            countdown={countdown}
            onToggle={toggle}
            noun="sealer"
          />
          {/* Device fault (last_error): a message icon to the left of the
              status pill. Click to pop the detail box open; click again to
              hide it back to the icon. Only shown when a fault is present. */}
          {lastErrorBand !== null && (
            <div className="relative flex items-center">
              <button
                type="button"
                onClick={() => setFaultExpanded((v) => !v)}
                aria-expanded={faultExpanded}
                aria-label="Device fault details"
                title={
                  faultExpanded
                    ? "Hide fault"
                    : (lastErrorBand.code ?? "Device fault")
                }
                className="flex h-6 w-6 items-center justify-center rounded-md border border-rose-300 bg-rose-50 text-rose-700 hover:bg-rose-100 dark:border-rose-700 dark:bg-rose-950/40 dark:text-rose-300"
              >
                <svg
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden
                >
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
              </button>
              {faultExpanded && (
                <div
                  role="status"
                  className="absolute right-0 top-7 z-30 w-64 rounded-md border border-rose-300 bg-rose-50 px-2.5 py-2 text-left text-[11px] leading-snug text-rose-900 shadow-lg dark:border-rose-700 dark:bg-rose-950 dark:text-rose-100"
                >
                  {lastErrorBand.code && (
                    <div className="mb-1 font-mono font-semibold">
                      {lastErrorBand.code}
                    </div>
                  )}
                  {lastErrorBand.recovery ? (
                    <>
                      {lastErrorBand.recovery}{" "}
                      <span className="opacity-75">{lastErrorBand.raw}</span>
                    </>
                  ) : (
                    lastErrorBand.raw
                  )}
                </div>
              )}
            </div>
          )}
          <StatusPill state={status} />
        </>
      }
    >
      {/* 2x2 metric grid. The Actual pill is tinted by the heater state
          (emerald=stable, amber=heating/cooling, slate=unknown/disconnected,
          neutral when the device hasn't published components.heater). */}
      <div className="grid grid-cols-2 gap-1.5">
        <MetricPill
          caption="Actual"
          value={fmt(sealer.actualTempC, "°C", 0)}
          // Out-of-band always trumps the device's heater.state — the
          // pill should not look "ready" while the seal interlock blocks.
          tone={!tempInBand ? "warn" : heaterTone(sealer.heaterState)}
          title={
            sealStartTitle ?? sealer.heaterMessage ?? undefined
          }
        />
        <EditablePill
          caption="Setpoint"
          current={sealer.setpointTempC}
          unit="°C"
          step={1}
          min={TEMP_MIN}
          max={TEMP_MAX}
          decimals={0}
          locked={locked}
          busy={isBusy}
          onSet={(v) =>
            exec("seal.set_temperature", () =>
              postSealerSetTemperature(snapshot.id, v),
            )
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
            exec("seal.set_time", () => postSealerSetTime(snapshot.id, v))
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

      {/* Action buttons. Visibility is state-aware so the row doesn't
          balloon during requires_init or busy. */}
      <div className="flex flex-wrap items-center gap-1">
        {isRequiresInit && (
          <TileButton
            onClick={() =>
              exec("startup", () => postSealerStartup(snapshot.id))
            }
            disabled={controlsDisabled}
            variant="primary"
          >
            Startup
          </TileButton>
        )}
        {(isReady || isBusy) && (
          <TileButton
            onClick={() =>
              exec("shutdown", () => postSealerShutdown(snapshot.id))
            }
            disabled={controlsDisabled}
          >
            Shutdown
          </TileButton>
        )}
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
                exec("stage.in", () => postSealerStageIn(snapshot.id))
              }
            />
            <PositionPill
              label="Stage out"
              isCurrent={sealer.stageState === "out"}
              isMoving={isBusy && sealer.stageState !== "out"}
              disabled={controlsDisabled || sealer.stageState === "out"}
              onClick={() =>
                exec("stage.out", () => postSealerStageOut(snapshot.id))
              }
            />
            <TileButton
              onClick={() =>
                exec("seal.start", () => postSealerSealStart(snapshot.id))
              }
              disabled={controlsDisabled || sealStartBlocked}
              variant="primary"
              title={sealStartTitle}
            >
              Seal start
            </TileButton>
          </>
        )}
        {/* Seal stop is always visible — an abort control should never
            disappear, including when idle or before init. Only the control
            lock gates it. */}
        <TileButton
          onClick={() =>
            exec("seal.stop", () => postSealerSealStop(snapshot.id))
          }
          disabled={controlsDisabled}
          variant="danger"
        >
          Seal stop
        </TileButton>
      </div>

      {/* Inline error band: 412 / 423 / 409 from the last action.
          Auto-clears on next click or when the device transitions to
          ready + tempInBand + heaterOk. Amber for all kinds today. */}
      {actionError !== null && (
        <div
          role="status"
          className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
        >
          {actionError.status > 0 && (
            <span className="shrink-0 font-mono font-semibold">
              {actionError.status}
            </span>
          )}
          <span className="leading-snug">{actionError.message}</span>
        </div>
      )}
    </TileShell>
  );
}
