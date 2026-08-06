/**
 * Shared fixtures for the OT-2 deck tests (pure-logic + component). Builds
 * gateway-shaped `details.snapshot.deck` slots and minimal /status envelopes.
 * Test-only — not imported by application code.
 */

import type { DeviceDeck, DeviceDeckSlot, RobotModule, WellSample } from "./api";
import type { EquipmentSnapshot } from "@/types/api";

export type { DeviceDeck, DeviceDeckSlot, RobotModule, WellSample };

type Status = EquipmentSnapshot["status"];

export function emptySlot(): DeviceDeckSlot {
  return { labware: null, module: null, slot_state: "empty", source: "empty", declared: null };
}

export function labwareSlot(
  state: DeviceDeckSlot["slot_state"],
  labware: {
    kind: string;
    load_name: string;
    display_name?: string | null;
    is_tiprack?: boolean;
    rows?: number | null;
    columns?: number | null;
    nickname?: string | null;
    wells?: WellSample[] | null;
  },
): DeviceDeckSlot {
  return {
    labware: {
      display_name: null,
      is_tiprack: false,
      rows: null,
      columns: null,
      plate_id: null,
      ...labware,
    },
    module: null,
    slot_state: state,
    source: state === "declared" ? "declared" : "run",
    declared: null,
  };
}

export function moduleSlot(
  state: DeviceDeckSlot["slot_state"],
  moduleName: string,
  serial: string | null = null,
): DeviceDeckSlot {
  return {
    labware: null,
    module: { module_name: moduleName, status: null, serial_number: serial },
    slot_state: state,
    source: state === "declared" ? "declared" : "run",
    declared: null,
  };
}

/** A mismatch slot: `declared` lost, `observed` labware won the deck. */
export function mismatchSlot(
  declared: { kind: string; load_name: string },
  observed: { kind: string; load_name: string; rows?: number | null; columns?: number | null },
): DeviceDeckSlot {
  return {
    labware: {
      display_name: null,
      is_tiprack: false,
      rows: null,
      columns: null,
      plate_id: null,
      ...observed,
    },
    module: null,
    slot_state: "mismatch",
    source: "run",
    declared,
  };
}

/** A normalized device deck with the given slots (all others empty). */
export function deckWith(slots: Record<string, DeviceDeckSlot>): DeviceDeck {
  const all: Record<string, DeviceDeckSlot> = {};
  for (let i = 1; i <= 12; i++) all[String(i)] = emptySlot();
  Object.assign(all, slots);
  return { source: "declared", slots: all };
}

/** A minimal /status envelope carrying the given `details`. */
export function statusWithDetails(details: Record<string, unknown>): Status {
  return {
    protocol_version: "1.1",
    equipment_id: "ot2_test",
    equipment_name: "OT-2 (test)",
    equipment_kind: "liquid_handler",
    equipment_status: "ready",
    device_time: "2026-07-15T00:00:00Z",
    components: {},
    metrics: {},
    details,
  } as unknown as Status;
}
