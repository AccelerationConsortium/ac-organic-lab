import { describe, expect, it } from "vitest";

import type { DeviceDeck, DeviceDeckSlot, RobotModule } from "./ot2-deck-test-helpers";
import {
  emptySlot,
  deckWith,
  labwareSlot,
  mismatchSlot,
  moduleSlot,
  statusWithDetails,
} from "./ot2-deck-test-helpers";
import {
  buildSlotView,
  claimedByFromStatus,
  computeOverhangReadouts,
  declaredMapFromDeck,
  deviceDeckFromStatus,
  mountedTipsFromStatus,
  nextDeclaration,
  pairModuleSlots,
  pipetteLabel,
  robotModulesFromStatus,
  tipRacksFromStatus,
} from "./ot2-deck";

// ---------------------------------------------------------------------------
// declaredMapFromDeck — the full-PUT round-trip rule
// ---------------------------------------------------------------------------

describe("declaredMapFromDeck", () => {
  it("round-trips a declared slot as its exact load_name (not the coarse kind)", () => {
    const deck = deckWith({
      "3": labwareSlot("declared", {
        kind: "96-well",
        load_name: "corning_96_wellplate_360ul_flat",
      }),
    });
    expect(declaredMapFromDeck(deck)).toEqual({
      "3": "corning_96_wellplate_360ul_flat",
    });
  });

  it("falls back to the kind when the gateway reported no load_name (legacy declaration)", () => {
    const deck = deckWith({
      "5": labwareSlot("declared", { kind: "24-well", load_name: "" }),
    });
    expect(declaredMapFromDeck(deck)).toEqual({ "5": "24-well" });
  });

  it("round-trips a declared module via its gateway picker key", () => {
    const deck = deckWith({
      "11": moduleSlot("declared", "temperature module gen2"),
    });
    expect(declaredMapFromDeck(deck)).toEqual({ "11": "temperature_module" });
  });

  it("keeps the declared (losing) side of a mismatch, preferring its load_name", () => {
    const deck = deckWith({
      "2": mismatchSlot(
        { kind: "tiprack", load_name: "opentrons_96_tiprack_300ul" },
        { kind: "96-well", load_name: "corning_96_wellplate_360ul_flat" },
      ),
    });
    expect(declaredMapFromDeck(deck)).toEqual({ "2": "opentrons_96_tiprack_300ul" });
  });

  it("never re-declares observed (run/in_use) labware", () => {
    const deck = deckWith({
      "1": labwareSlot("in_use", { kind: "96-well", load_name: "corning_96_wellplate_360ul_flat" }),
      "4": labwareSlot("occupied", { kind: "reservoir", load_name: "agilent_1_reservoir_290ml" }),
      "9": labwareSlot("declared", { kind: "tiprack", load_name: "opentrons_96_tiprack_300ul" }),
    });
    expect(declaredMapFromDeck(deck)).toEqual({ "9": "opentrons_96_tiprack_300ul" });
  });

  it("survives an edit to another slot without dropping existing declarations", () => {
    // The full-PUT body after declaring slot 6 must still carry slot 3's exact
    // load_name and slot 11's module key — the regression this lib exists to fix.
    const deck = deckWith({
      "3": labwareSlot("declared", { kind: "96-well", load_name: "corning_96_wellplate_360ul_flat" }),
      "11": moduleSlot("declared", "temperature module gen2"),
    });
    const next = nextDeclaration(declaredMapFromDeck(deck), 6, "agilent_1_reservoir_290ml");
    expect(next).toEqual({
      "3": "corning_96_wellplate_360ul_flat",
      "11": "temperature_module",
      "6": "agilent_1_reservoir_290ml",
    });
  });
});

describe("nextDeclaration", () => {
  it("sets and clears a slot without mutating the input", () => {
    const base = { "1": "waste" };
    const withSlot = nextDeclaration(base, 2, "corning_96_wellplate_360ul_flat");
    expect(withSlot).toEqual({ "1": "waste", "2": "corning_96_wellplate_360ul_flat" });
    const cleared = nextDeclaration(withSlot, 2, null);
    expect(cleared).toEqual({ "1": "waste" });
    expect(base).toEqual({ "1": "waste" }); // untouched
  });

  it("treats empty string as clear", () => {
    expect(nextDeclaration({ "7": "tiprack" }, 7, "")).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// buildSlotView — declared vs observed vs mismatch render states
// ---------------------------------------------------------------------------

describe("buildSlotView", () => {
  it("renders an empty slot", () => {
    const v = buildSlotView(8, deckWith({}), {});
    expect(v.state).toBe("empty");
    expect(v.title).toContain("Slot 8");
  });

  it("derives grid + label from device labware and exposes the exact load_name", () => {
    const deck = deckWith({
      "3": labwareSlot("declared", {
        kind: "96-well",
        load_name: "corning_96_wellplate_360ul_flat",
        rows: 8,
        columns: 12,
      }),
    });
    const v = buildSlotView(3, deck, {});
    expect(v.state).toBe("declared");
    expect(v.rows).toBe(8);
    expect(v.columns).toBe(12);
    expect(v.loadName).toBe("corning_96_wellplate_360ul_flat");
    expect(v.label).toBe("corning_96_wellplate_360ul_flat");
  });

  it("carries the slot nickname, tiprack flag and tracked wells to the inspector", () => {
    // The nickname is the join key from a slot to details.tip_racks; the
    // gateway stamps it per slot precisely so it survives run/REPL precedence,
    // where display_name is overwritten by the robot's own label.
    const deck = deckWith({
      "5": labwareSlot("occupied", {
        kind: "tiprack",
        load_name: "opentrons_96_tiprack_20ul",
        display_name: "Opentrons 20uL Tiprack",
        is_tiprack: true,
        nickname: "tips_20",
      }),
      "2": labwareSlot("occupied", {
        kind: "96-well",
        load_name: "corning_96_wellplate_360ul_flat",
        nickname: "plate_D",
        wells: [{ well: "A1", sample_id: "caffeine-001", volume_ul: 180 }],
      }),
    });

    const rack = buildSlotView(5, deck, {});
    expect(rack.nickname).toBe("tips_20");
    expect(rack.isTiprack).toBe(true);
    expect(rack.label).toBe("Opentrons 20uL Tiprack"); // robot's label still wins the label

    const plate = buildSlotView(2, deck, {});
    expect(plate.nickname).toBe("plate_D");
    expect(plate.isTiprack).toBe(false);
    expect(plate.wells?.[0].sample_id).toBe("caffeine-001");
  });

  it("infers the tiprack flag from the kind on a gateway that omits it", () => {
    const deck = deckWith({
      "5": labwareSlot("occupied", { kind: "tiprack", load_name: "opentrons_96_tiprack_20ul" }),
    });
    // labwareSlot defaults is_tiprack to false; the classified kind is the
    // fallback, so an un-migrated gateway still reads tip state.
    const v = buildSlotView(5, deck, {});
    expect(v.nickname).toBeNull();
    expect(v.isTiprack).toBe(false);
  });

  it("flags a mismatch with declared vs observed in the title", () => {
    const deck = deckWith({
      "2": mismatchSlot(
        { kind: "tiprack", load_name: "opentrons_96_tiprack_300ul" },
        { kind: "96-well", load_name: "corning_96_wellplate_360ul_flat" },
      ),
    });
    const v = buildSlotView(2, deck, {});
    expect(v.state).toBe("mismatch");
    expect(v.title).toContain("declared opentrons_96_tiprack_300ul");
    expect(v.title).toContain("observed corning_96_wellplate_360ul_flat");
    expect(v.declared?.load_name).toBe("opentrons_96_tiprack_300ul");
  });

  it("renders a bare module slot as a module cell", () => {
    const deck = deckWith({ "11": moduleSlot("declared", "temperature module gen2") });
    const v = buildSlotView(11, deck, {});
    expect(v.kind).toBe("module");
    expect(v.moduleName).toBe("temperature module gen2");
    expect(v.state).toBe("declared");
  });

  it("lets labware win the cell when it sits on a module", () => {
    const slot: DeviceDeckSlot = {
      ...labwareSlot("in_use", { kind: "96-well", load_name: "corning_96_wellplate_360ul_flat" }),
      module: { module_name: "temperature module gen2", status: null, serial_number: null },
    };
    const v = buildSlotView(11, deckWith({ "11": slot }), {});
    expect(v.kind).toBe("96-well");
    expect(v.moduleName).toBe("temperature module gen2");
    expect(v.title).toContain("on temperature module gen2");
  });

  it("falls back to the legacy store when the device deck is absent", () => {
    const v = buildSlotView(12, null, { "12": "waste" });
    expect(v.isTrash).toBe(true);
    expect(v.state).toBe("declared");
  });
});

// ---------------------------------------------------------------------------
// Module pairing + the temperature-module overhang
// ---------------------------------------------------------------------------

const TEMP_LIVE: RobotModule = {
  model: "temperatureModuleV2",
  type: "temperatureModuleType",
  serial: "TDV22...",
  status: "holding at target",
  current_temperature: 40,
  target_temperature: 40,
};

describe("pairModuleSlots / computeOverhangReadouts", () => {
  it("pairs a declared module (no serial) with live telemetry by family", () => {
    const deck = deckWith({ "11": moduleSlot("declared", "temperature module gen2") });
    const paired = pairModuleSlots(deck, [TEMP_LIVE]);
    expect(paired.get(11)?.live).toBe(TEMP_LIVE);
  });

  it("puts the temperature readout in the free left-neighbor overhang cell", () => {
    const deck = deckWith({ "11": moduleSlot("declared", "temperature module gen2") });
    const paired = pairModuleSlots(deck, [TEMP_LIVE]);
    const overhang = computeOverhangReadouts(deck, paired);
    expect(overhang.get(10)?.moduleSlot).toBe(11);
  });

  it("skips the overhang when the left neighbor is occupied or the module sits in column 1", () => {
    const occupiedLeft = deckWith({
      "10": labwareSlot("in_use", { kind: "96-well", load_name: "corning_96_wellplate_360ul_flat" }),
      "11": moduleSlot("declared", "temperature module gen2"),
    });
    expect(
      computeOverhangReadouts(occupiedLeft, pairModuleSlots(occupiedLeft, [TEMP_LIVE])).size,
    ).toBe(0);

    const column1 = deckWith({ "10": moduleSlot("declared", "temperature module gen2") });
    expect(computeOverhangReadouts(column1, pairModuleSlots(column1, [TEMP_LIVE])).size).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// details.* readers
// ---------------------------------------------------------------------------

describe("status detail readers", () => {
  it("parses the normalized deck and rejects the legacy loose shape", () => {
    const good = statusWithDetails({
      snapshot: { deck: deckWith({ "1": emptySlot() }) },
    });
    expect(deviceDeckFromStatus(good)?.source).toBe("declared");

    const loose = statusWithDetails({
      snapshot: { deck: { slots: { "1": { type: "plate" } }, occupied_slots: 1 } },
    });
    expect(deviceDeckFromStatus(loose)).toBeNull();
    expect(deviceDeckFromStatus(statusWithDetails({}))).toBeNull();
  });

  it("parses tip rack summaries", () => {
    const status = statusWithDetails({
      tip_racks: {
        tips_300: {
          total: 96,
          available: 90,
          empty: 4,
          touched: 2,
          tips: { A1: "empty", B1: "sample-7" },
          registered_at: "2026-07-10T12:00:00Z",
        },
      },
    });
    const racks = tipRacksFromStatus(status);
    expect(racks).toHaveLength(1);
    expect(racks[0]).toMatchObject({ nickname: "tips_300", total: 96, available: 90 });
  });

  it("parses mounted tips and the claim holder", () => {
    const status = statusWithDetails({
      mounted_tips: {
        right: { rack: "tips_300", well: "C5", last_sample: "cu-complex-2", origin_status: "fresh" },
      },
      claimed_by: {
        session_id: "f1f1c1a2",
        owner: "agent:complexation",
        expires_at: "2026-07-15T22:00:00Z",
      },
    });
    expect(mountedTipsFromStatus(status)).toEqual([
      {
        pipette: "right",
        rack: "tips_300",
        well: "C5",
        last_sample: "cu-complex-2",
        origin_status: "fresh",
      },
    ]);
    expect(claimedByFromStatus(status)?.owner).toBe("agent:complexation");
    expect(claimedByFromStatus(statusWithDetails({}))).toBeNull();
  });

  it("parses live modules defensively", () => {
    const status = statusWithDetails({
      robot: { modules: [TEMP_LIVE, { bogus: true }, null] },
    });
    expect(robotModulesFromStatus(status)).toEqual([TEMP_LIVE]);
    expect(robotModulesFromStatus(statusWithDetails({}))).toEqual([]);
  });
});

describe("pipetteLabel", () => {
  it("formats Opentrons pipette model strings", () => {
    expect(pipetteLabel("p300_multi_gen2")).toBe("P300 Multi");
    expect(pipetteLabel("p1000_single_gen2")).toBe("P1000 Single");
    expect(pipetteLabel("none")).toBe("—");
    expect(pipetteLabel(undefined)).toBe("—");
  });
});

// Type-only usage so the helper types stay exported/importable.
const _typecheck: DeviceDeck | null = null;
void _typecheck;
