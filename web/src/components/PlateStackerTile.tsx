"use client";

import { useEffect, useState, useTransition } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import {
  ApiError,
  postStackerHandoff,
  postStackerHome,
  postStackerPresentPlate,
  postStackerShutdown,
  postStackerStagePlate,
  postStackerStartup,
} from "@/lib/api";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
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

/** Inline error band shape (mirrors PlateSealerTile.ActionError). */
interface ActionError {
  status: number;
  message: string;
}

/**
 * Translate a thrown control error into renderable copy. The BioStack 412
 * precondition body shape is `{ detail, plate_staged, required }` — a
 * stage/present/handoff action attempted with the shuttle in the wrong
 * state (e.g. handoff with no plate staged, or stage_plate when one is
 * already staged).
 */
function interpretActionError(e: unknown): ActionError {
  if (!(e instanceof ApiError)) {
    return { status: 0, message: e instanceof Error ? e.message : String(e) };
  }
  const body = (e.body ?? {}) as Record<string, unknown>;
  const detail = typeof body.detail === "string" ? body.detail : undefined;

  if (e.status === 412) {
    const staged =
      typeof body.plate_staged === "boolean" ? body.plate_staged : null;
    const required =
      body.required === undefined || body.required === null
        ? null
        : String(body.required);
    if (detail) {
      return { status: 412, message: detail };
    }
    if (staged !== null && required !== null) {
      return {
        status: 412,
        message: `Plate staged is ${staged}, action requires ${required}.`,
      };
    }
    return { status: 412, message: "Stacker precondition not met." };
  }

  if (e.status === 423) {
    const claimedBy = body.claimed_by as { owner?: string } | undefined;
    const owner = claimedBy?.owner;
    return {
      status: 423,
      message: owner
        ? `Device claim is held by ${owner}. Try again later.`
        : detail ?? "Device is busy with another caller.",
    };
  }

  if (e.status === 409) {
    const msg = detail ?? "Action rejected.";
    const hint = /init|startup|connect/i.test(msg) ? " Click Startup first." : "";
    return { status: 409, message: msg + hint };
  }

  return { status: e.status, message: detail ?? e.message };
}

/**
 * Map the BioStack `last_error.code` taxonomy to prescriptive recovery
 * copy. Codes per the device repo:
 *   stack_empty · no_plate_picked_up · connect_failed ·
 *   protocol_error · command_error
 * Unknown / null code → render the raw message verbatim (forward-compat
 * for new codes, back-compat for devices that don't populate `code`).
 */
function interpretLastError(
  errorInfo: EquipmentSnapshot["status"]["last_error"],
): { code: string | null; recovery: string; raw: string } | null {
  if (!errorInfo) return null;
  const raw = errorInfo.message;
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
}

// Display order = the natural plate-cycle progression, with lifecycle
// (startup/shutdown) bookending it.
const ACTIONS: {
  name: string;
  label: string;
  variant?: "primary" | "danger" | "default";
  run: (id: string) => Promise<unknown>;
}[] = [
  { name: "startup", label: "Startup", variant: "primary", run: postStackerStartup },
  { name: "home", label: "Home", run: postStackerHome },
  { name: "stage_plate", label: "Stage plate", run: postStackerStagePlate },
  { name: "present_plate", label: "Present plate", run: postStackerPresentPlate },
  { name: "handoff", label: "Handoff", variant: "primary", run: postStackerHandoff },
  { name: "shutdown", label: "Shutdown", run: postStackerShutdown },
];

export function PlateStackerTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const stacker = parseStacker(snapshot);
  const { locked, countdown, toggle } = useControlLock();
  const [, startTransition] = useTransition();
  const [actionError, setActionError] = useState<ActionError | null>(null);

  const status = snapshot.status.equipment_status;
  const isReady = status === "ready";

  const allowed = allowedActions(snapshot);
  const effectiveAllowed =
    allowed.length > 0 ? allowed : STATE_FALLBACK[status] ?? [];
  const isAllowed = (name: string) => effectiveAllowed.includes(name);

  function exec(fn: () => Promise<unknown>) {
    setActionError(null);
    startTransition(() => {
      fn().catch((err: unknown) => setActionError(interpretActionError(err)));
    });
  }

  // Auto-clear the inline error once the device is back to ready (the
  // refusal is no longer the current truth).
  useEffect(() => {
    if (actionError && isReady) setActionError(null);
  }, [actionError, isReady]);

  const lastErrorBand = interpretLastError(snapshot.status.last_error);

  return (
    <TileShell
      snapshot={snapshot}
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

      {/* last_error band (rose): a hardware/driver fault the device
          reported. Distinct from the amber refusal band below. */}
      {lastErrorBand !== null && (
        <div
          role="status"
          className="flex items-start gap-2 rounded-md border border-rose-300 bg-rose-50 px-2.5 py-1.5 text-[11px] text-rose-900 dark:border-rose-700 dark:bg-rose-950/40 dark:text-rose-200"
          title={lastErrorBand.raw}
        >
          {lastErrorBand.code && (
            <span className="shrink-0 font-mono font-semibold">
              {lastErrorBand.code}
            </span>
          )}
          <span className="min-w-0 flex-1 leading-snug">
            {lastErrorBand.recovery ? (
              <>
                {lastErrorBand.recovery}{" "}
                <span className="opacity-75">{lastErrorBand.raw}</span>
              </>
            ) : (
              lastErrorBand.raw
            )}
          </span>
        </div>
      )}

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
