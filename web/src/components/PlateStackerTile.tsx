"use client";

import { useEffect } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import {
  postStackerHandoff,
  postStackerHome,
  postStackerPresentPlate,
  postStackerShutdown,
  postStackerStagePlate,
  postStackerStartup,
} from "@/lib/api";
import type { Parse412 } from "@/lib/action-error";
import { useActionError } from "@/lib/use-action-error";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import type { LastErrorInterpret } from "./LastErrorBadge";
import { StatusPill } from "./StatusPill";
import { TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

/**
 * Kind-specific tile for `plate_stacker` (Agilent BioStack 4).
 *
 * Reference device + contract: skills/.../skill_catalog/plate_stacker.py.
 * Six parameterless control actions (startup / shutdown / home /
 * stage_plate / present_plate / handoff), all claim-gated on the device;
 * the dashboard's audited `/api/equipment/{id}/control/{action}`
 * passthrough handles the per-request claim dance.
 *
 * Button availability is driven by the device's `status.allowed_actions`
 * (STATUS_SPEC v1.1, device-authoritative). When the device hasn't
 * populated it yet we fall back to a coarse state map — same
 * allowed_actions-first / requires_states-fallback philosophy the SDK
 * uses in `await lab.skills()`.
 */

interface StackerState {
  /** details.plate_staged — is a plate currently sitting on the shuttle? */
  plateStaged: boolean | null;
  /** components.handoff.state — handoff position, e.g. "clear" / "occupied". */
  handoffState: string | null;
  handoffMessage: string | null;
}

function parseStacker(snapshot: EquipmentSnapshot): StackerState {
  const details = (snapshot.status.details ?? {}) as Record<string, unknown>;
  const components = snapshot.status.components ?? {};
  const handoff = components["handoff"];
  return {
    plateStaged:
      typeof details["plate_staged"] === "boolean"
        ? (details["plate_staged"] as boolean)
        : null,
    handoffState: handoff?.state ?? null,
    handoffMessage: handoff?.message ?? null,
  };
}

// `allowed_actions` is a v1.1 field not yet in the generated OpenAPI
// types; read it defensively. Empty / absent → coarse state fallback.
function allowedActions(snapshot: EquipmentSnapshot): string[] {
  const raw = (snapshot.status as { allowed_actions?: unknown }).allowed_actions;
  return Array.isArray(raw) ? raw.filter((a): a is string => typeof a === "string") : [];
}

// Coarse fallback for v1.0 devices (or before the device populates
// allowed_actions): mirrors plate_stacker.py's requires_states.
const STATE_FALLBACK: Record<string, string[]> = {
  requires_init: ["startup"],
  ready: ["shutdown", "home", "stage_plate", "present_plate", "handoff"],
  dry_run: ["startup", "shutdown", "home", "stage_plate", "present_plate", "handoff"],
};

/**
 * The BioStack 412 precondition body shape is `{ detail, plate_staged,
 * required }` — a stage/present/handoff action attempted with the shuttle in
 * the wrong state (e.g. handoff with no plate staged, or stage_plate when one
 * is already staged). Prefer the device's `detail` sentence; else synthesize
 * one from plate_staged/required. Every other refusal (423 claim, 409 state)
 * is handled generically by interpretActionError.
 */
const parseStacker412: Parse412 = (body) => {
  const detail = typeof body.detail === "string" ? body.detail : undefined;
  if (detail) return detail;
  const staged =
    typeof body.plate_staged === "boolean" ? body.plate_staged : null;
  const required =
    body.required === undefined || body.required === null
      ? null
      : String(body.required);
  if (staged !== null && required !== null) {
    return `Plate staged is ${staged}, action requires ${required}.`;
  }
  return null;
};

/**
 * Map the BioStack `last_error.code` taxonomy to prescriptive recovery
 * copy. Codes per the device repo:
 *   stack_empty · no_plate_picked_up · connect_failed ·
 *   protocol_error · command_error
 * Unknown / null code → render the raw message verbatim (forward-compat
 * for new codes, back-compat for devices that don't populate `code`).
 *
 * Shaped as a {@link LastErrorInterpret} so it feeds the standardized
 * <LastErrorBadge> in the TileShell header (the same surface PlateSealerTile
 * uses) — not a bespoke in-body band.
 */
const interpretLastError: LastErrorInterpret = (errorInfo) => {
  if (!errorInfo) return null;
  const raw = (errorInfo.message ?? "").trim();
  if (!raw) return null;
  const code = errorInfo.code ?? null;
  const recovery: string =
    {
      stack_empty: "Plate stack is empty — load plates into the magazine.",
      // Latched fault: the controller will not retry until power-cycled.
      no_plate_picked_up:
        "Plate pick failed and latched. Power-cycle the stacker to clear, then Startup.",
      connect_failed:
        "Could not connect to the stacker. Check power/cable, then Startup.",
      protocol_error: "Driver protocol error — restart the device service.",
      command_error: "Stacker command failed — see message.",
    }[code ?? ""] ?? "";
  return { code, recovery, raw };
};

// Display order = the natural plate-cycle progression. Lifecycle
// (startup/shutdown) lives in the template banner's ON toggle instead.
const ACTIONS: {
  name: string;
  label: string;
  variant?: "primary" | "danger" | "default";
  run: (id: string) => Promise<unknown>;
}[] = [
  { name: "home", label: "Home", run: postStackerHome },
  { name: "stage_plate", label: "Stage plate", run: postStackerStagePlate },
  { name: "present_plate", label: "Present plate", run: postStackerPresentPlate },
  { name: "handoff", label: "Handoff", variant: "primary", run: postStackerHandoff },
];

export function PlateStackerTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const stacker = parseStacker(snapshot);
  const { locked, countdown, toggle } = useControlLock(snapshot.id);
  const { actionError, setActionError, exec } = useActionError(parseStacker412);

  const status = snapshot.status.equipment_status;
  const isReady = status === "ready";
  const deviceOn = status !== "requires_init" && status !== "unknown";

  const allowed = allowedActions(snapshot);
  const effectiveAllowed =
    allowed.length > 0 ? allowed : STATE_FALLBACK[status] ?? [];
  const isAllowed = (name: string) => effectiveAllowed.includes(name);

  // Auto-clear the inline error once the device is back to ready (the
  // refusal is no longer the current truth).
  useEffect(() => {
    if (actionError && isReady) setActionError(null);
  }, [actionError, isReady]);

  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      lastErrorInterpret={interpretLastError}
      lifecycle={{
        // ON toggles startup/shutdown. No STOP: the stacker exposes no
        // halt/abort endpoint (per the template contract STOP must never
        // alias a shutdown).
        isOn: deviceOn,
        initLabel: "INIT",
        onPowerToggle: () =>
          deviceOn
            ? exec(() => postStackerShutdown(snapshot.id))
            : exec(() => postStackerStartup(snapshot.id)),
        disabled: locked,
        powerTitle: deviceOn
          ? "Device is on — click to shut down"
          : "Device is off — click to start up",
      }}
      headerRight={
        <>
          <LockButton
            locked={locked}
            countdown={countdown}
            onToggle={toggle}
            noun="stacker"
          />
          <StatusPill state={status} />
        </>
      }
    >
      {/* Status rows: plate staged + handoff position. */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <span className="w-14 shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
            Plate
          </span>
          <StateChip
            on={stacker.plateStaged === true}
            off={stacker.plateStaged === false}
            onLabel="Staged"
            offLabel="Empty"
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="w-14 shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
            Handoff
          </span>
          <span
            className="truncate text-xs text-ink-muted dark:text-slate-300"
            title={stacker.handoffMessage ?? undefined}
          >
            {stacker.handoffState ?? "—"}
          </span>
        </div>
      </div>

      {/* Action buttons, gated by allowed_actions (device-authoritative)
          and the control lock. */}
      <div className="flex flex-wrap items-center gap-1">
        {ACTIONS.filter((a) => isAllowed(a.name)).map((a) => (
          <TileButton
            key={a.name}
            onClick={() => exec(() => a.run(snapshot.id))}
            disabled={locked}
            variant={a.variant ?? "default"}
          >
            {a.label}
          </TileButton>
        ))}
      </div>

      {/* Inline refusal band (amber): 412 precondition / 423 claim / 409
          state from the last action. Auto-clears on next click or when the
          device returns to ready. */}
    </TileShell>
  );
}

/** Tiny read-only state chip: emerald when `on`, neutral when `off`,
 *  muted "—" when neither (state unknown / not published). */
function StateChip({
  on,
  off,
  onLabel,
  offLabel,
}: {
  on: boolean;
  off: boolean;
  onLabel: string;
  offLabel: string;
}) {
  const label = on ? onLabel : off ? offLabel : "—";
  return (
    <span
      className={[
        "inline-flex h-6 items-center rounded-md border px-2 text-[11px] font-semibold",
        on
          ? "border-emerald-400 bg-emerald-100 text-emerald-900 dark:border-emerald-600 dark:bg-emerald-900/60 dark:text-emerald-100"
          : off
            ? "border-slate-200 bg-slate-50 text-ink-muted dark:border-slate-700 dark:bg-slate-800/40 dark:text-slate-400"
            : "border-slate-200 bg-transparent text-ink-subtle dark:border-slate-700 dark:text-slate-500",
      ].join(" ")}
    >
      {label}
    </span>
  );
}
