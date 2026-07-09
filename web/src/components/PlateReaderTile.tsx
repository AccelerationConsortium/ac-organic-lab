"use client";

import { useState, useTransition } from "react";
import type { EquipmentSnapshot } from "@/types/api";
import { postPlateReaderShutdown, postPlateReaderStartup } from "@/lib/api";
import { useActionError } from "@/lib/use-action-error";
import { useControlLock } from "@/lib/use-control-lock";
import { LockButton } from "./ControlLock";
import { StatusPill } from "./StatusPill";
import { TileShell } from "./TileShell";

type Tone = "neutral" | "ok" | "warn" | "muted";

const TONE_CLASSES: Record<Tone, string> = {
  neutral:
    "border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/40",
  ok: "border-emerald-300 bg-emerald-50 dark:border-emerald-700 dark:bg-emerald-950/40",
  warn: "border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/40",
  muted:
    "border-slate-200 bg-slate-100 dark:border-slate-700 dark:bg-slate-800/20",
};

function componentTone(state: string | undefined | null): Tone {
  if (!state) return "muted";
  if (state === "idle" || state === "in" || state === "stable" || state === "ready")
    return "ok";
  if (state === "error" || state === "fault") return "warn";
  if (state === "unknown" || state === "disconnected") return "muted";
  return "neutral"; // busy/reading/etc. — informative, not alarming
}

// The four Cytation sub-systems, in display order (two rows of two).
const COMPONENT_ROWS: { key: string; caption: string }[] = [
  { key: "optics", caption: "Optics" },
  { key: "incubator", caption: "Incubator" },
  { key: "plate_stage", caption: "Stage" },
  { key: "imaging", caption: "Imaging" },
];

function ComponentPill({
  caption,
  state,
  title,
}: {
  caption: string;
  state: string | null;
  title?: string;
}) {
  return (
    <div
      className={`flex h-7 items-center gap-1 rounded-md border px-2 ${TONE_CLASSES[componentTone(state)]}`}
      title={title}
    >
      <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
        {caption}
      </span>
      <span className="ml-auto truncate text-xs font-semibold text-ink dark:text-slate-100">
        {state ?? "—"}
      </span>
    </div>
  );
}

/**
 * BioTek Cytation 5 (kind: plate_reader). Compact half-height tile: template
 * lifecycle banner (ON toggle — startup/shutdown; the device has no halt
 * endpoint, so no STOP), the four sub-system components as pills in two rows,
 * and the read counter on one line. Read/imaging controls land with the
 * protocol-execution work.
 */
export function PlateReaderTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const { status } = snapshot;
  const components = status.components ?? {};
  const metrics = status.metrics ?? {};
  const { locked, noAccess, countdown, toggle } = useControlLock(snapshot.id);
  const { actionError, setActionError, reportError } = useActionError();
  const [, startTransition] = useTransition();
  const [pending, setPending] = useState(false);

  const st = status.equipment_status;
  const deviceOn = st !== "requires_init" && st !== "unknown";

  function runControl(name: string, fn: () => Promise<unknown>) {
    setActionError(null);
    setPending(true);
    startTransition(() => {
      fn()
        .catch((e: unknown) => reportError(e, name))
        .finally(() => setPending(false));
    });
  }

  const readCount = metrics["read_count"]?.value;

  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      lifecycle={{
        isOn: deviceOn,
        onPowerToggle: () =>
          deviceOn
            ? runControl("shutdown", () => postPlateReaderShutdown(snapshot.id))
            : runControl("startup", () => postPlateReaderStartup(snapshot.id)),
        disabled: locked || pending,
        powerTitle: locked
          ? noAccess
            ? "No access"
            : "Sign in to control"
          : deviceOn
            ? "Device is on — click to shut down"
            : "Device is off — click to start up",
      }}
      headerRight={
        <>
          <LockButton
            locked={locked}
            countdown={countdown}
            onToggle={toggle}
            noun="plate reader"
          />
          <StatusPill state={st} />
        </>
      }
    >
      {/* Four sub-systems as pills, two per row. */}
      <div className="grid grid-cols-2 gap-1.5">
        {COMPONENT_ROWS.map(({ key, caption }) => {
          const c = components[key];
          return (
            <ComponentPill
              key={key}
              caption={caption}
              state={c?.state ?? null}
              title={c?.message ?? undefined}
            />
          );
        })}
      </div>

      {/* Read counter — one line. */}
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
          Read Count
        </span>
        <span className="text-xs font-semibold text-ink dark:text-slate-100 tabular-nums">
          {typeof readCount === "number" ? readCount.toLocaleString() : "—"}
        </span>
      </div>
    </TileShell>
  );
}
