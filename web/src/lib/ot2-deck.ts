/**
 * Pure OT-2 deck / status parsing logic behind the compact
 * `LiquidHandlerTile` on the overview.
 *
 * It once also backed a full-page OT-2 interface in this repo; that was
 * removed in favour of the gateway's own panel, which the tile links to
 * directly (see `device-panels.ts`), so the device's UI is the only
 * implementation of those controls. The tile still needs this parsing, and
 * `buildWellModel` (`plate-wells.ts`) still tints its deck thumbnails with
 * real tip state.
 *
 * Everything here is side-effect-free and derives exclusively from the
 * device's own `/status` envelope (the gateway owns deck, plate and tip
 * state — see `opentrons-server` DECK_STATE_PLAN). No function issues HTTP.
 *
 * The declared-layout round-trip rule (`declaredMapFromDeck`) is the load-
 * bearing part: the gateway's `POST /control/deck/declare` is a full-layout
 * replace, so every edit must re-send all currently-declared slots. A
 * declared slot is re-sent as its exact Opentrons `load_name` when the
 * gateway reported one (any string containing "_" is parsed as a load_name
 * device-side), falling back to the legacy `kind` string; a declared module
 * round-trips via its picker key (`MODULE_NAME_TO_KEY`). Dropping to `kind`
 * when a `load_name` exists would silently degrade an exact declaration.
 */

import type { DeviceDeck, DeviceDeckSlot, RobotModule, WellSample } from "./api";
import type { EquipmentSnapshot } from "@/types/api";

type Status = EquipmentSnapshot["status"];

// OT-2 deck: 12 numbered slots, 3 columns × 4 rows, slot 1 at the bottom-left,
// slot 3 at the bottom-right, slot 12 at the top-right. Rendered top row first
// so the on-screen layout matches the physical deck.
export const DECK_ROWS: number[][] = [
  [10, 11, 12],
  [7, 8, 9],
  [4, 5, 6],
  [1, 2, 3],
];

// Grid (rows × columns) per normalized labware `kind`. Used as a fallback when
// the device doesn't send rows/columns, and for the legacy store's kind strings.
export const KIND_GRID: Record<string, { rows: number; columns: number }> = {
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

export const TRASH_KINDS = new Set(["waste", "trash"]);

// Inverse of the gateway's module kind -> module_name map, so a declared module
// read back from /status round-trips to its picker key on the next full-PUT
// declare (otherwise editing another slot would drop it). Keep in sync with
// the gateway's deck.py `_MODULE_KINDS`.
export const MODULE_NAME_TO_KEY: Record<string, string> = {
  "temperature module gen2": "temperature_module",
  "magnetic module gen2": "magnetic_module",
  "heater-shaker module gen1": "heater_shaker_module",
  "thermocycler module gen2": "thermocycler_module",
};

// Read the device's normalized deck from /status, but ONLY the new shape:
// { source, slots: { "1": { slot_state, labware, ... } } }. An un-migrated
// gateway publishes the old loose deck ({ slots: {}, occupied_slots, ... }) or
// nothing — in that case return null and callers fall back to the legacy store.
export function deviceDeckFromStatus(status: Status): DeviceDeck | null {
  const snap = status.details?.["snapshot"] as { deck?: unknown } | undefined;
  const deck = snap?.deck as Partial<DeviceDeck> | undefined;
  if (!deck || typeof deck !== "object") return null;
  if (!("source" in deck) || !deck.slots) return null;
  const first = Object.values(deck.slots)[0] as { slot_state?: unknown } | undefined;
  if (!first || !("slot_state" in first)) return null; // old loose shape
  return deck as DeviceDeck;
}

export function gridFor(
  kind: string | undefined,
  rows?: number | null,
  columns?: number | null,
): { rows: number; columns: number } {
  if (rows && columns) return { rows, columns };
  if (kind && KIND_GRID[kind]) return KIND_GRID[kind];
  return { rows: 0, columns: 0 };
}

// Live attached-module telemetry from `details.robot.modules` (populated by the
// gateway whenever the module is powered — independent of any run, refreshes on
// the normal /status poll). Defensive: an un-migrated gateway omits it.
export function robotModulesFromStatus(status: Status): RobotModule[] {
  const robot = status.details?.["robot"] as { modules?: unknown } | undefined;
  const mods = robot?.modules;
  if (!Array.isArray(mods)) return [];
  return mods.filter(
    (m): m is RobotModule =>
      !!m && typeof m === "object" && typeof (m as RobotModule).type === "string",
  );
}

// Module family keyword shared by the deck's `module_name` ("temperature module
// gen2") and the live module's `type`/`model` ("temperatureModuleType"), used to
// pair the declared deck module with its live telemetry when serials don't match
// (a declared module has no serial until the robot observes it).
export function moduleFamily(s: string | null | undefined): string | null {
  const t = (s ?? "").toLowerCase();
  if (t.includes("temperature")) return "temperature";
  if (t.includes("magnetic")) return "magnetic";
  if (t.includes("heater")) return "heater_shaker";
  if (t.includes("thermocycler")) return "thermocycler";
  return null;
}

// Module families that report temperatures (magnetic modules don't).
export const TEMP_FAMILIES = new Set(["temperature", "heater_shaker", "thermocycler"]);

export function moduleShortLabel(name: string): string {
  switch (moduleFamily(name)) {
    case "temperature":
      return "Temp module";
    case "magnetic":
      return "Mag module";
    case "heater_shaker":
      return "Heater-shaker";
    case "thermocycler":
      return "Thermocycler";
    default:
      return name;
  }
}

// ---------------------------------------------------------------------------
// Declared-layout round-trip
// ---------------------------------------------------------------------------

/** The exact declare string for one declared item: prefer the load_name the
 *  gateway reported (exact Opentrons definition), fall back to the kind. */
function declareString(item: { kind?: string | null; load_name?: string | null }): string | null {
  if (item.load_name) return item.load_name;
  if (item.kind) return item.kind;
  return null;
}

/**
 * The operator-editable declared map for a migrated device: only the slots the
 * operator actually declared (declared-only + the losing side of a mismatch) —
 * observed labware is NOT re-declared. Values are exact load_names when known
 * (see module docstring), module picker keys for declared modules, else kinds.
 */
export function declaredMapFromDeck(deck: DeviceDeck): Record<string, string> {
  const declared: Record<string, string> = {};
  for (const [slot, s] of Object.entries(deck.slots)) {
    if (s.slot_state === "declared" && s.module) {
      // A declared (sticky) module → round-trip via its picker key.
      const key = MODULE_NAME_TO_KEY[s.module.module_name];
      if (key) declared[slot] = key;
    } else if (s.slot_state === "declared" && s.labware) {
      const v = declareString(s.labware);
      if (v) declared[slot] = v;
    } else if (s.slot_state === "mismatch" && s.declared) {
      const v = declareString(s.declared);
      if (v) declared[slot] = v;
    }
  }
  return declared;
}

/**
 * The full-layout declare body after assigning `value` to `slot` (empty string
 * or null clears the slot). Pure: returns a new map, does not mutate.
 */
export function nextDeclaration(
  declaredMap: Record<string, string>,
  slot: number,
  value: string | null,
): Record<string, string> {
  const next: Record<string, string> = { ...declaredMap };
  if (value) next[String(slot)] = value;
  else delete next[String(slot)];
  return next;
}

// ---------------------------------------------------------------------------
// Per-slot render info
// ---------------------------------------------------------------------------

// One slot's render info, sourced from either the device deck or the legacy store.
export interface SlotView {
  kind?: string; // normalized kind (or legacy key); "module" for a bare module
  label: string;
  rows: number;
  columns: number;
  state: "empty" | "declared" | "occupied" | "in_use" | "mismatch";
  isTrash: boolean;
  title: string;
  moduleName?: string; // set when a hardware module occupies this slot (labware may sit on it)
  /** Exact Opentrons load_name when the deck reports one (shown in tooltips/details). */
  loadName?: string;
  /** For mismatch slots: what was declared vs what is observed. */
  declared?: { kind: string; load_name: string } | null;
  /** The setup recipe's nickname for this slot — the join key for
   *  `details.tip_racks` and the `/control/*` `labware_nickname` argument. */
  nickname?: string | null;
  /** True when this slot holds a tip rack (drives tip- vs sample-state reads). */
  isTiprack?: boolean;
  /** Tracked plate samples folded onto this slot, when there are any. */
  wells?: WellSample[] | null;
}

export function buildSlotView(
  slot: number,
  deviceDeck: DeviceDeck | null,
  legacyLabware: Record<string, string>,
): SlotView {
  if (deviceDeck) {
    const s: DeviceDeckSlot | undefined = deviceDeck.slots[String(slot)];
    // A module WITHOUT labware renders as its own kind of cell (declared =
    // sticky fixture, else live/occupied). When labware sits ON the module
    // the plate wins the cell — the module must never hide its plate — and
    // the module shows up as `moduleName` (accent strip + title).
    if (s?.module && !s.labware) {
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
        moduleName: s.module.module_name,
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
    const onModule = s.module ? ` on ${s.module.module_name}` : "";
    const title =
      s.slot_state === "mismatch"
        ? `Slot ${slot} — declared ${s.declared?.load_name || s.declared?.kind || "?"}, observed ${
            s.labware?.load_name || kind || "?"
          }`
        : `Slot ${slot} — ${name}${onModule} (${stateWord})`;
    return {
      kind,
      label: name,
      rows,
      columns,
      state: s.slot_state,
      isTrash,
      title,
      moduleName: s.module?.module_name,
      loadName: s.labware?.load_name || undefined,
      declared: s.declared ?? null,
      nickname: s.labware?.nickname ?? null,
      // Trust the deck's own flag; fall back to the classified kind for a
      // gateway that predates it.
      isTiprack: s.labware?.is_tiprack ?? kind === "tiprack",
      wells: s.labware?.wells ?? null,
    };
  }
  // Legacy store: pure intent, no lifecycle.
  const key = legacyLabware[String(slot)];
  if (!key)
    return { label: "", rows: 0, columns: 0, state: "empty", isTrash: false, title: `Slot ${slot} — empty` };
  const { rows, columns } = gridFor(key);
  const isTrash = TRASH_KINDS.has(key);
  return { kind: key, label: key, rows, columns, state: "declared", isTrash, title: `Slot ${slot} — ${key}` };
}

// ---------------------------------------------------------------------------
// Module ↔ telemetry pairing + the temperature-module overhang
// ---------------------------------------------------------------------------

export interface PairedModule {
  name: string;
  live: RobotModule | null;
}

/**
 * Pair each deck slot that carries a module with its live telemetry from
 * `details.robot.modules`. Pair by serial first, then by module family (a
 * declared module usually has no serial until the robot observes it).
 */
export function pairModuleSlots(
  deviceDeck: DeviceDeck | null,
  robotModules: RobotModule[],
): Map<number, PairedModule> {
  const moduleSlots = new Map<number, PairedModule>();
  if (!deviceDeck) return moduleSlots;
  const used = new Set<RobotModule>();
  for (const [slotStr, s] of Object.entries(deviceDeck.slots)) {
    if (!s.module) continue;
    const serial = s.module.serial_number;
    const family = moduleFamily(s.module.module_name);
    const live =
      robotModules.find((m) => !used.has(m) && serial != null && m.serial === serial) ??
      robotModules.find((m) => !used.has(m) && family != null && moduleFamily(m.type) === family) ??
      null;
    if (live) used.add(live);
    moduleSlots.set(Number(slotStr), { name: s.module.module_name, live });
  }
  return moduleSlots;
}

export interface OverhangReadout extends PairedModule {
  moduleSlot: number;
}

/**
 * Overhang readout cells. The OT-2 temperature module is physically LONG: it
 * is bolted at its slot (11 in this lab) and overhangs ~half of the slot to
 * its left (10), while the plate sits ON the module above the module's own
 * slot. Mirror that footprint: render the live readout in the empty left-
 * neighbor cell so the module's own slot stays free for its plate.
 * Left neighbor exists when the slot isn't first in its deck row (slots
 * 1/4/7/10, i.e. slot % 3 === 1).
 */
export function computeOverhangReadouts(
  deviceDeck: DeviceDeck | null,
  moduleSlots: Map<number, PairedModule>,
): Map<number, OverhangReadout> {
  const overhang = new Map<number, OverhangReadout>();
  for (const [slot, m] of moduleSlots) {
    if (moduleFamily(m.name) !== "temperature") continue;
    if (slot % 3 === 1) continue;
    const left = slot - 1;
    const leftSlot = deviceDeck?.slots[String(left)];
    const leftIsFree = !leftSlot || (!leftSlot.module && !leftSlot.labware);
    if (!leftIsFree) continue;
    overhang.set(left, { moduleSlot: slot, name: m.name, live: m.live });
  }
  return overhang;
}

// ---------------------------------------------------------------------------
// Tips, claim, robot info (details.* readers for the full-page interface)
// ---------------------------------------------------------------------------

/** One tip rack's summary from `details.tip_racks` (gateway TipStateStore). */
export interface TipRackSummary {
  /** The deck slot the rack sits in — a tip rack's identity. It carries no
   *  sample and no history worth naming, and the slot is what an operator
   *  points at and refills. Also why this join always resolves: the slot is
   *  known from the deck itself, unlike `labware.nickname`, which is null
   *  until a protocol setup runs. */
  slot: string;
  total: number;
  available: number;
  empty: number;
  touched: number;
  /** Non-fresh wells only: well -> status ("empty" | sample id | ...). */
  tips: Record<string, string>;
  registered_at?: string;
}

export function tipRacksFromStatus(status: Status): TipRackSummary[] {
  const raw = status.details?.["tip_racks"];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  const out: TipRackSummary[] = [];
  for (const [slot, v] of Object.entries(raw as Record<string, unknown>)) {
    if (!v || typeof v !== "object") continue;
    const r = v as Partial<TipRackSummary>;
    out.push({
      slot,
      total: typeof r.total === "number" ? r.total : 0,
      available: typeof r.available === "number" ? r.available : 0,
      empty: typeof r.empty === "number" ? r.empty : 0,
      touched: typeof r.touched === "number" ? r.touched : 0,
      tips: r.tips && typeof r.tips === "object" ? (r.tips as Record<string, string>) : {},
      registered_at: typeof r.registered_at === "string" ? r.registered_at : undefined,
    });
  }
  return out.sort((a, b) => Number(a.slot) - Number(b.slot));
}

/** One mounted tip from `details.mounted_tips` — which rack/well the tip on a
 *  pipette came from, and the last sample it touched. */
export interface MountedTip {
  pipette: string;
  rack?: string;
  /** The addressed well. */
  well?: string;
  /** Every well this head emptied — a whole column for a multi-channel
   *  pipette, `[well]` for a single-channel one. Absent on a gateway
   *  predating multi-channel tip tracking. */
  wells?: string[];
  /** Channel count of the pipette holding these tips. */
  channels?: number;
  last_sample?: string;
  origin_status?: string;
}

export function mountedTipsFromStatus(status: Status): MountedTip[] {
  const raw = status.details?.["mounted_tips"];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  const out: MountedTip[] = [];
  for (const [pipette, v] of Object.entries(raw as Record<string, unknown>)) {
    if (!v || typeof v !== "object") continue;
    const m = v as Record<string, unknown>;
    out.push({
      pipette,
      rack: typeof m.rack === "string" ? m.rack : undefined,
      well: typeof m.well === "string" ? m.well : undefined,
      wells: Array.isArray(m.wells)
        ? m.wells.filter((w): w is string => typeof w === "string")
        : undefined,
      channels: typeof m.channels === "number" ? m.channels : undefined,
      last_sample: typeof m.last_sample === "string" ? m.last_sample : undefined,
      origin_status: typeof m.origin_status === "string" ? m.origin_status : undefined,
    });
  }
  return out.sort((a, b) => a.pipette.localeCompare(b.pipette));
}

/** Current claim holder from `details.claimed_by` (STATUS_SPEC v1.1). */
export interface ClaimedBy {
  session_id: string;
  owner: string;
  expires_at: string;
}

export function claimedByFromStatus(status: Status): ClaimedBy | null {
  const raw = status.details?.["claimed_by"];
  if (!raw || typeof raw !== "object") return null;
  const c = raw as Partial<ClaimedBy>;
  if (typeof c.owner !== "string") return null;
  return {
    session_id: typeof c.session_id === "string" ? c.session_id : "",
    owner: c.owner,
    expires_at: typeof c.expires_at === "string" ? c.expires_at : "",
  };
}

/** Robot probe info from `details.robot` (gateway HTTP-probe cache). */
export interface RobotInfo {
  robot_name?: string;
  api_version?: string;
  run_active?: boolean;
  reachable?: boolean;
}

export function robotInfoFromStatus(status: Status): RobotInfo | null {
  const raw = status.details?.["robot"];
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  return {
    robot_name: typeof r.robot_name === "string" ? r.robot_name : undefined,
    api_version: typeof r.api_version === "string" ? r.api_version : undefined,
    run_active: typeof r.run_active === "boolean" ? r.run_active : undefined,
    reachable: typeof r.reachable === "boolean" ? r.reachable : undefined,
  };
}

// "p300_multi_gen2" -> "P300 Multi"; drops the genN suffix, keeps the model
// and channel count. Empty/absent mounts render as "—".
export function pipetteLabel(state: string | undefined | null): string {
  if (!state || state === "none" || state === "disconnected") return "—";
  return state
    .split("_")
    .filter((p) => !/^gen\d+$/i.test(p))
    .map((p) => (/^p\d+$/i.test(p) ? p.toUpperCase() : p.charAt(0).toUpperCase() + p.slice(1)))
    .join(" ");
}
