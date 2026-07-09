"use client";

import { useState, useTransition } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getDeckLayout,
  postOt2Pause,
  postOt2Shutdown,
  postOt2Startup,
  postSetLights,
  putDeckLayout,
} from "@/lib/api";
import type { EquipmentSnapshot } from "@/types/api";
import { useActionError } from "@/lib/use-action-error";
import { useControlLock } from "@/lib/use-control-lock";
import { useUserAuth } from "@/lib/user-auth";

import { ComponentList } from "./ComponentList";
import { FetchErrorBand } from "./FetchErrorBand";
import { LockButton } from "./ControlLock";
import { MetricList } from "./MetricList";
import { StatusPill } from "./StatusPill";
import { TileButton } from "./TileButton";
import { TileShell } from "./TileShell";

type LightsState = "on" | "off" | "unknown";

function parseLights(snapshot: EquipmentSnapshot): LightsState {
  const raw = snapshot.status.components?.["lights"]?.state;
  if (raw === "on" || raw === "off") return raw;
  return "unknown";
}

// "p300_multi_gen2" -> "P300 Multi"; drops the genN suffix, keeps the model
// and channel count. Empty/absent mounts render as "—".
function pipetteLabel(state: string | undefined | null): string {
  if (!state || state === "none" || state === "disconnected") return "—";
  return state
    .split("_")
    .filter((p) => !/^gen\d+$/i.test(p))
    .map((p) => (/^p\d+$/i.test(p) ? p.toUpperCase() : p.charAt(0).toUpperCase() + p.slice(1)))
    .join(" ");
}

// Rendered on the tile as their own pills / lights control, so they are
// excluded from the generic ComponentList below to avoid duplication.
const TILE_OWNED_COMPONENTS = new Set([
  "lights",
  "pipette_left",
  "pipette_right",
  "ssh",
  "protocol",
]);

// OT-2 deck: 12 numbered slots, 3 columns × 4 rows, slot 1 at the bottom-left,
// slot 3 at the bottom-right, slot 12 at the top-right. Rendered top row first
// so the on-screen layout matches the physical deck.
const DECK_ROWS: number[][] = [
  [10, 11, 12],
  [7, 8, 9],
  [4, 5, 6],
  [1, 2, 3],
];

// Labware the operator can place on a slot. Client-side only for now; the deck
// will later be driven by the device's own state (details.snapshot.deck.slots).
interface LabwareType {
  key: string;
  label: string;
  rows: number;
  columns: number;
}

const LABWARE_TYPES: LabwareType[] = [
  { key: "96-well", label: "96-well plate", rows: 8, columns: 12 },
  { key: "24-well", label: "24-well plate", rows: 4, columns: 6 },
  // Waste bin: no wells — just greys out the slot.
  { key: "waste", label: "Waste bin", rows: 0, columns: 0 },
];

function labwareType(key: string | undefined): LabwareType | undefined {
  return LABWARE_TYPES.find((l) => l.key === key);
}
function labwareLabel(key: string | undefined): string {
  return labwareType(key)?.label ?? key ?? "";
}

// Miniature well grid drawn inside a deck slot once well-plate labware is
// assigned. The inner grid is given the plate's own aspect ratio (columns/rows)
// so every cell is square — which makes the `rounded-full` wells true circles —
// and it is centred within the (taller) slot box.
function MiniPlate({ rows, columns }: { rows: number; columns: number }) {
  return (
    <div className="flex h-full w-full items-center justify-center p-1.5">
      <div
        className="grid w-full gap-[2px]"
        style={{
          aspectRatio: `${columns} / ${rows}`,
          gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))`,
        }}
        aria-hidden
      >
        {Array.from({ length: rows * columns }, (_, i) => (
          <span key={i} className="rounded-full bg-slate-300 dark:bg-slate-600" />
        ))}
      </div>
    </div>
  );
}

export function LiquidHandlerTile({ snapshot }: { snapshot: EquipmentSnapshot }) {
  const { status } = snapshot;
  const metrics = status.metrics ?? {};
  const components = status.components ?? {};
  // Lights + pipettes have dedicated affordances in the top row; keep them out
  // of the generic ComponentList so they aren't shown twice.
  const otherComponents = Object.fromEntries(
    Object.entries(components).filter(([k]) => !TILE_OWNED_COMPONENTS.has(k)),
  );
  const pipLeft = components["pipette_left"];
  const pipRight = components["pipette_right"];

  const lights = parseLights(snapshot);
  const { locked, noAccess, countdown, toggle } = useControlLock(snapshot.id);
  const [, startTransition] = useTransition();
  const [pending, setPending] = useState<boolean>(false);
  // Keeps its own useTransition + `pending` (the On/Off buttons disable while
  // in flight); error state comes from the shared hook so a refusal renders in
  // the same <ActionErrorBand> as every other tile.
  const { actionError, setActionError, reportError } = useActionError();

  function setLights(on: boolean) {
    setActionError(null);
    setPending(true);
    startTransition(() => {
      postSetLights(snapshot.id, on)
        .catch((e: unknown) => reportError(e, "lights.set"))
        .finally(() => setPending(false));
    });
  }

  function runControl(name: string, fn: () => Promise<unknown>) {
    setActionError(null);
    setPending(true);
    startTransition(() => {
      fn()
        .catch((e: unknown) => reportError(e, name))
        .finally(() => setPending(false));
    });
  }

  const lightsKnown = lights === "on" || lights === "off";
  const isOn = lights === "on";

  // Power state for the template's ON toggle: anything but requires_init /
  // unknown counts as "on" (the OT-2 reports ready/busy while up).
  const deviceOn =
    status.equipment_status !== "requires_init" &&
    status.equipment_status !== "unknown";

  // Deck labware is stored server-side (shared across users) via
  // GET/PUT /api/equipment/<id>/deck, and polled so other operators' edits
  // appear. TODO: replace with the device's own deck state
  // (status.details.snapshot.deck.slots) once the OT-2 server publishes it.
  const queryClient = useQueryClient();
  const deckKey = ["deck", snapshot.id] as const;
  const { data: deckData } = useQuery({
    queryKey: deckKey,
    queryFn: () => getDeckLayout(snapshot.id),
    refetchInterval: 15000,
  });
  const deckLabware = deckData?.slots ?? {};
  const [selectedSlot, setSelectedSlot] = useState<number | null>(null);

  function setSlotLabware(slot: number, labware: string) {
    // Changing the deck layout is gated to admins / authorized users of this
    // device — the same `locked` the control affordances use (backed by
    // /authz/mine). The picker below is disabled when locked, so this is the
    // belt-and-suspenders guard; the backend PUT enforces the same 403.
    if (locked) return;
    const next = { ...deckLabware };
    if (labware) next[String(slot)] = labware;
    else delete next[String(slot)];
    // Optimistic: reflect immediately, then persist. On failure, refetch the
    // authoritative server copy and surface the error.
    queryClient.setQueryData(deckKey, { slots: next });
    putDeckLayout(snapshot.id, next)
      .then((res) => queryClient.setQueryData(deckKey, res))
      .catch((e: unknown) => {
        reportError(e, "deck.set");
        queryClient.invalidateQueries({ queryKey: deckKey });
      });
  }

  return (
    <TileShell
      snapshot={snapshot}
      actionError={actionError}
      headerRight={
        <>
          {/* Lock chip is for the protocol-execution actions (home, setup,
              aspirate, dispense, ...) which will land in a follow-up tile.
              The Lights toggle below is convenience-class: no lock chip, but
              it still requires a signed-in session (disabled when logged
              out), like every control write. */}
          <LockButton
            locked={locked}
            countdown={countdown}
            onToggle={toggle}
            noun="liquid handler"
          />
          <StatusPill state={status.equipment_status} />
        </>
      }
      lifecycle={{
        isOn: deviceOn,
        onPowerToggle: () =>
          deviceOn
            ? runControl("shutdown", () => postOt2Shutdown(snapshot.id))
            : runControl("startup", () => postOt2Startup(snapshot.id)),
        onStop: () => runControl("pause", () => postOt2Pause(snapshot.id)),
        disabled: locked || pending,
        powerTitle: locked
          ? noAccess
            ? "No access"
            : "Sign in to control"
          : deviceOn
            ? "Device is on — click to shut down"
            : "Device is off — click to start up",
        stopTitle: locked
          ? noAccess
            ? "No access"
            : "Sign in to control"
          : "Halt: pause the running protocol (does not disconnect)",
      }}
      bannerExtra={
        // Light toggle (convenience-class, no lock chip) + pipette pills,
        // grouped right so the Light button never crowds the INIT/STOP
        // group on narrow tiles.
        <div className="ml-auto flex items-center gap-1.5">
          <TileButton
            onClick={() => setLights(!isOn)}
            disabled={locked || pending}
            title={
              locked
                ? noAccess
                  ? "No access to this equipment"
                  : "Sign in to control"
                : lightsKnown
                  ? isOn
                    ? "Lights on — click to turn off"
                    : "Lights off — click to turn on"
                  : "Lights state not reported — click to turn on"
            }
          >
            <span
              className={[
                "mr-1.5 inline-block h-2.5 w-2.5 rounded-full",
                isOn
                  ? "bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.7)]"
                  : "bg-slate-900 dark:bg-black",
              ].join(" ")}
              aria-hidden
            />
            Light
          </TileButton>
            <span
              className="flex h-7 items-center rounded-md border border-slate-200 bg-white px-2 text-xs font-semibold text-ink dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-100"
              title={`Left mount: ${pipLeft?.state ?? "empty"}`}
            >
              {pipetteLabel(pipLeft?.state)}
            </span>
            <span
              className="flex h-7 items-center rounded-md border border-slate-200 bg-white px-2 text-xs font-semibold text-ink dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-100"
              title={`Right mount: ${pipRight?.state ?? "empty"}`}
            >
              {pipetteLabel(pipRight?.state)}
            </span>
          </div>
      }
    >


      {/* Deck — 12 slots (1 bottom-left … 12 top-right), 3 per row. Blocks are a
          fixed 160×120 px with a fixed 10px gap both horizontally and
          vertically (total 3×160 + 2×10 = 500px wide). Scrolls if the tile is
          narrower. Click a slot to select it, then assign labware via "Select
          Labware" below. */}
      <div
        className="grid justify-center gap-[10px] overflow-x-auto"
        style={{ gridTemplateColumns: "repeat(3, 160px)" }}
      >
        {DECK_ROWS.flat().map((slot) => {
          const lw = deckLabware[slot];
          const lwType = labwareType(lw);
          const selected = selectedSlot === slot;
          return (
            <button
              key={slot}
              type="button"
              onClick={() => setSelectedSlot((s) => (s === slot ? null : slot))}
              title={`Slot ${slot}${lw ? ` — ${labwareLabel(lw)}` : " — empty"}`}
              className={[
                "relative h-[120px] w-[160px] overflow-hidden rounded border transition-colors",
                selected
                  ? "border-sky-500 bg-sky-50 dark:border-sky-500 dark:bg-sky-950/40"
                  : "border-slate-200 bg-white hover:border-slate-400 dark:border-slate-700 dark:bg-slate-800/40 dark:hover:border-slate-500",
              ].join(" ")}
            >
              {lwType ? (
                lwType.key === "waste" ? (
                  // Waste bin: grey the whole slot, no wells.
                  <div className="flex h-full w-full items-center justify-center bg-slate-300/70 dark:bg-slate-700/60">
                    <span className="text-[9px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
                      waste
                    </span>
                  </div>
                ) : (
                  <MiniPlate rows={lwType.rows} columns={lwType.columns} />
                )
              ) : (
                // Empty: large light-grey watermark slot number.
                <div className="flex h-full w-full items-center justify-center">
                  <span className="select-none text-4xl font-semibold text-slate-200 dark:text-slate-700">
                    {slot}
                  </span>
                </div>
              )}
            </button>
          );
        })}
      </div>

      {/* Select Labware (assigns to the highlighted slot) on the left; SSH /
          Protocol status pills pushed to the right. The pills show status by
          dot colour only (green = connected/ready, else grey) — no state
          text; the raw state is in the hover title. */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
          Select Labware
        </span>
        <select
          value={selectedSlot != null ? deckLabware[selectedSlot] ?? "" : ""}
          onChange={(e) => {
            if (selectedSlot == null) return;
            setSlotLabware(selectedSlot, e.target.value);
          }}
          disabled={selectedSlot == null || locked}
          title={
            locked
              ? noAccess
                ? "No access — only authorized users of this device can change its deck layout"
                : "Sign in to change the deck layout"
              : undefined
          }
          aria-label="Select labware for the highlighted slot"
          className="min-w-0 rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-ink disabled:bg-slate-50 disabled:text-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:disabled:bg-slate-900 dark:disabled:text-slate-600"
        >
          <option value="">
            {locked
              ? noAccess
                ? "No access"
                : "Sign in to edit"
              : selectedSlot == null
                ? "Select a slot first"
                : "Empty"}
          </option>
          {LABWARE_TYPES.map((l) => (
            <option key={l.key} value={l.key}>
              {l.label}
            </option>
          ))}
        </select>
        {selectedSlot != null && (
          <span className="text-[10px] text-ink-subtle dark:text-slate-500">
            → slot {selectedSlot}
          </span>
        )}

        <div className="ml-auto flex items-center gap-1.5">
          {(["ssh", "protocol"] as const).map((key) => {
            const c = components[key];
            if (!c) return null;
            const ok = c.state === "connected" || c.state === "ready";
            return (
              <span
                key={key}
                className="flex h-7 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2 dark:border-slate-700 dark:bg-slate-800/60"
                title={`${key === "ssh" ? "SSH" : "Protocol"}: ${c.state}`}
              >
                <span
                  className={`inline-block h-2 w-2 rounded-full ${
                    ok ? "bg-emerald-400" : "bg-slate-400 dark:bg-slate-500"
                  }`}
                  aria-hidden
                />
                <span className="text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
                  {key === "ssh" ? "SSH" : "Protocol"}
                </span>
              </span>
            );
          })}
        </div>
      </div>

      {Object.keys(metrics).length > 0 && <MetricList metrics={metrics} />}
      {Object.keys(otherComponents).length > 0 && (
        <ComponentList components={otherComponents} />
      )}

      {snapshot.fetch_error && <FetchErrorBand error={snapshot.fetch_error} />}
    </TileShell>
  );
}
