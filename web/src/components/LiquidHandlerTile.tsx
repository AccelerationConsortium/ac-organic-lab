"use client";

import { useState, useTransition } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteDeckDeclare,
  getDeckLayout,
  postDeckDeclare,
  postOt2Pause,
  postOt2Shutdown,
  postOt2Startup,
  postSetLights,
  putDeckLayout,
  type DeviceDeck,
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

// Grid (rows × columns) per normalized labware `kind`. Used as a fallback when
// the device doesn't send rows/columns, and for the legacy store's kind strings.
const KIND_GRID: Record<string, { rows: number; columns: number }> = {
  "96-well": { rows: 8, columns: 12 },
  "384-well": { rows: 16, columns: 24 },
  "48-well": { rows: 6, columns: 8 },
  "24-well": { rows: 4, columns: 6 },
  "12-well": { rows: 3, columns: 4 },
  "6-well": { rows: 2, columns: 3 },
  tiprack: { rows: 8, columns: 12 },
  reservoir: { rows: 1, columns: 12 },
  tuberack: { rows: 4, columns: 6 },
};
const TRASH_KINDS = new Set(["waste", "trash"]);

// Picker options. Legacy (dashboard-store) devices only accept the three the
// stopgap validates; a migrated gateway accepts the full normalized set.
const LEGACY_PICKER: { key: string; label: string }[] = [
  { key: "96-well", label: "96-well plate" },
  { key: "24-well", label: "24-well plate" },
  { key: "waste", label: "Waste bin" },
];
const DEVICE_PICKER: { key: string; label: string }[] = [
  { key: "96-well", label: "96-well plate" },
  { key: "384-well", label: "384-well plate" },
  { key: "24-well", label: "24-well plate" },
  { key: "tiprack", label: "Tip rack" },
  { key: "reservoir", label: "Reservoir" },
  { key: "tuberack", label: "Tube rack" },
  { key: "waste", label: "Waste bin" },
  // Sticky (declared) module fixtures. The picker sends the kind KEY; the gateway
  // maps it to a module (deck.py _MODULE_KINDS). Movable modules aren't declared —
  // they flow through the live run deck.
  { key: "temperature_module", label: "Temperature module" },
];

// Inverse of the gateway's module kind -> module_name map, so a declared module
// read back from /status round-trips to its picker key on the next full-PUT
// declare (otherwise editing another slot would drop it). Keep in sync with
// deck.py `_MODULE_KINDS`.
const MODULE_NAME_TO_KEY: Record<string, string> = {
  "temperature module gen2": "temperature_module",
  "magnetic module gen2": "magnetic_module",
  "heater-shaker module gen1": "heater_shaker_module",
  "thermocycler module gen2": "thermocycler_module",
};

// One slot's render info, sourced from either the device deck or the legacy store.
interface SlotView {
  kind?: string; // normalized kind (or legacy key)
  label: string;
  rows: number;
  columns: number;
  state: "empty" | "declared" | "occupied" | "in_use" | "mismatch";
  isTrash: boolean;
  title: string;
}

// Read the device's normalized deck from /status, but ONLY the new shape:
// { source, slots: { "1": { slot_state, labware, ... } } }. An un-migrated
// gateway publishes the old loose deck ({ slots: {}, occupied_slots, ... }) or
// nothing — in that case return null and the tile falls back to the legacy store.
function deviceDeckFromStatus(status: EquipmentSnapshot["status"]): DeviceDeck | null {
  const snap = status.details?.["snapshot"] as { deck?: unknown } | undefined;
  const deck = snap?.deck as Partial<DeviceDeck> | undefined;
  if (!deck || typeof deck !== "object") return null;
  if (!("source" in deck) || !deck.slots) return null;
  const first = Object.values(deck.slots)[0] as { slot_state?: unknown } | undefined;
  if (!first || !("slot_state" in first)) return null; // old loose shape
  return deck as DeviceDeck;
}

function gridFor(kind: string | undefined, rows?: number | null, columns?: number | null) {
  if (rows && columns) return { rows, columns };
  if (kind && KIND_GRID[kind]) return KIND_GRID[kind];
  return { rows: 0, columns: 0 };
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

  // --- Deck source: prefer the device's own normalized deck (Phase 3); fall
  // back to the shared dashboard store for gateways that don't publish it yet.
  const queryClient = useQueryClient();
  const deviceDeck = deviceDeckFromStatus(status);
  const migrated = deviceDeck != null;

  const deckKey = ["deck", snapshot.id] as const;
  const { data: legacyDeck } = useQuery({
    queryKey: deckKey,
    queryFn: () => getDeckLayout(snapshot.id),
    refetchInterval: 15000,
    enabled: !migrated, // migrated devices are driven by /status, not this store
  });
  const legacyLabware = legacyDeck?.slots ?? {};
  const [selectedSlot, setSelectedSlot] = useState<number | null>(null);

  const pickerOptions = migrated ? DEVICE_PICKER : LEGACY_PICKER;

  // The operator-editable declared map. For a migrated device it is only the
  // slots the operator actually declared (declared-only + the losing side of a
  // mismatch) — observed labware is NOT re-declared. For a legacy device it is
  // the whole store.
  const declaredMap: Record<string, string> = {};
  if (migrated && deviceDeck) {
    for (const [slot, s] of Object.entries(deviceDeck.slots)) {
      if (s.slot_state === "declared" && s.module) {
        // A declared (sticky) module → round-trip via its picker key.
        const key = MODULE_NAME_TO_KEY[s.module.module_name];
        if (key) declaredMap[slot] = key;
      } else if (s.slot_state === "declared" && s.labware) declaredMap[slot] = s.labware.kind;
      else if (s.slot_state === "mismatch" && s.declared) declaredMap[slot] = s.declared.kind;
    }
  } else {
    Object.assign(declaredMap, legacyLabware);
  }

  function slotView(slot: number): SlotView {
    if (migrated && deviceDeck) {
      const s = deviceDeck.slots[String(slot)];
      // A module occupies the slot regardless of slot_state — render it as its
      // own kind of cell (declared = sticky fixture, else live/occupied).
      if (s?.module) {
        const isDeclared = s.slot_state === "declared";
        const stateWord = isDeclared ? "declared" : s.slot_state === "in_use" ? "in use" : "occupied";
        return {
          kind: "module",
          label: s.module.module_name,
          rows: 0,
          columns: 0,
          state: isDeclared ? "declared" : "occupied",
          isTrash: false,
          title: `Slot ${slot} — ${s.module.module_name} (${stateWord})`,
        };
      }
      if (!s || s.slot_state === "empty") {
        return { label: "", rows: 0, columns: 0, state: "empty", isTrash: false, title: `Slot ${slot} — empty` };
      }
      const kind = s.labware?.kind;
      const { rows, columns } = gridFor(kind, s.labware?.rows, s.labware?.columns);
      const name = s.labware?.display_name || s.labware?.load_name || kind || "";
      const isTrash = !!kind && TRASH_KINDS.has(kind);
      const stateWord =
        s.slot_state === "in_use" ? "in use" : s.slot_state === "mismatch" ? "mismatch" : s.slot_state;
      const title =
        s.slot_state === "mismatch"
          ? `Slot ${slot} — declared ${s.declared?.kind ?? "?"}, observed ${kind ?? "?"}`
          : `Slot ${slot} — ${name} (${stateWord})`;
      return { kind, label: name, rows, columns, state: s.slot_state, isTrash, title };
    }
    // Legacy store: pure intent, no lifecycle.
    const key = legacyLabware[slot];
    if (!key) return { label: "", rows: 0, columns: 0, state: "empty", isTrash: false, title: `Slot ${slot} — empty` };
    const { rows, columns } = gridFor(key);
    const isTrash = TRASH_KINDS.has(key);
    return { kind: key, label: key, rows, columns, state: "declared", isTrash, title: `Slot ${slot} — ${key}` };
  }

  function setSlotLabware(slot: number, labware: string) {
    // Changing the deck layout is gated to admins / authorized users of this
    // device — the same `locked` the control affordances use. The picker is
    // disabled when locked; this is the belt-and-suspenders guard.
    if (locked) return;
    if (migrated) {
      // Send the full declared map (the gateway replaces the declaration). Only
      // operator-declared slots travel; observed labware is left to /status.
      const next: Record<string, string> = { ...declaredMap };
      if (labware) next[String(slot)] = labware;
      else delete next[String(slot)];
      postDeckDeclare(snapshot.id, next)
        .then(() => queryClient.invalidateQueries({ queryKey: ["equipment"] }))
        .catch((e: unknown) => reportError(e, "deck.declare"));
      return;
    }
    // Legacy dashboard store: optimistic update then persist.
    const next = { ...legacyLabware };
    if (labware) next[String(slot)] = labware;
    else delete next[String(slot)];
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
        // The OT-2 toggle connects/disconnects the GATEWAY control session —
        // it does NOT power the robot on/off. Labelled CONNECTED/DISCONNECTED
        // (not ON/OFF) so operators don't read it as robot power. Stop is a
        // protocol PAUSE, not an e-stop.
        onLabel: "CONNECTED",
        offLabel: "DISCONNECTED",
        stopLabel: "PAUSE",
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
            ? "Gateway session connected — click to disconnect (does NOT power off the robot)"
            : "Click to connect & initialize the gateway session",
        stopTitle: locked
          ? noAccess
            ? "No access"
            : "Sign in to control"
          : "Pause a running protocol — not an emergency stop (use the robot's physical e-stop); does not disconnect",
      }}
      bannerExtra={
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
          fixed 160×120 px. For a migrated gateway the contents come from the
          device's own /status deck (observed + declared, with mismatch flagged);
          otherwise from the shared dashboard store. Click a slot then assign
          labware via "Select Labware" below. */}
      <div
        className="grid justify-center gap-[10px] overflow-x-auto"
        style={{ gridTemplateColumns: "repeat(3, 160px)" }}
      >
        {DECK_ROWS.flat().map((slot) => {
          const v = slotView(slot);
          const selected = selectedSlot === slot;
          const mismatch = v.state === "mismatch";
          return (
            <button
              key={slot}
              type="button"
              onClick={() => setSelectedSlot((s) => (s === slot ? null : slot))}
              title={v.title}
              className={[
                "relative h-[120px] w-[160px] overflow-hidden rounded border transition-colors",
                selected
                  ? "border-sky-500 bg-sky-50 dark:border-sky-500 dark:bg-sky-950/40"
                  : mismatch
                    ? "border-amber-500 bg-amber-50 dark:border-amber-500 dark:bg-amber-950/30"
                    : "border-slate-200 bg-white hover:border-slate-400 dark:border-slate-700 dark:bg-slate-800/40 dark:hover:border-slate-500",
              ].join(" ")}
            >
              {v.isTrash ? (
                <div className="flex h-full w-full items-center justify-center bg-slate-300/70 dark:bg-slate-700/60">
                  <span className="text-[9px] uppercase tracking-wider text-ink-subtle dark:text-slate-400">
                    waste
                  </span>
                </div>
              ) : v.rows > 0 && v.columns > 0 ? (
                <MiniPlate rows={v.rows} columns={v.columns} />
              ) : v.state !== "empty" ? (
                // Occupied by something without a grid (module, unknown kind).
                <div className="flex h-full w-full items-center justify-center px-1 text-center">
                  <span className="text-[10px] font-medium text-ink-subtle dark:text-slate-400">
                    {v.label || v.kind}
                  </span>
                </div>
              ) : (
                <div className="flex h-full w-full items-center justify-center">
                  <span className="select-none text-4xl font-semibold text-slate-200 dark:text-slate-700">
                    {slot}
                  </span>
                </div>
              )}
              {/* State badge for migrated devices (top-right corner). */}
              {migrated && (v.state === "in_use" || v.state === "mismatch") && (
                <span
                  className={[
                    "absolute right-1 top-1 rounded px-1 text-[8px] font-semibold uppercase tracking-wide",
                    v.state === "mismatch"
                      ? "bg-amber-500 text-white"
                      : "bg-sky-500 text-white",
                  ].join(" ")}
                  aria-hidden
                >
                  {v.state === "mismatch" ? "≠" : "busy"}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Select Labware (assigns to the highlighted slot) on the left; SSH /
          Protocol status pills pushed to the right. */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-ink-subtle dark:text-slate-500">
          Select Labware
        </span>
        <select
          value={selectedSlot != null ? declaredMap[String(selectedSlot)] ?? "" : ""}
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
              : migrated
                ? "Declares operator intent; merged with the device's observed deck"
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
          {pickerOptions.map((l) => (
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
