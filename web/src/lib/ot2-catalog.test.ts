import { describe, expect, it } from "vitest";

import { MODULE_NAME_TO_KEY } from "./ot2-deck";
import {
  CATEGORY_ORDER,
  OT2_CATALOG,
  catalogEntryForDeclare,
  groupedCatalog,
} from "./ot2-catalog";

describe("OT2_CATALOG", () => {
  it("has unique stable keys and unique declare strings", () => {
    const keys = OT2_CATALOG.map((e) => e.key);
    expect(new Set(keys).size).toBe(keys.length);
    const declares = OT2_CATALOG.map((e) => e.declare);
    expect(new Set(declares).size).toBe(declares.length);
  });

  it("carries the OT2Demo standard load names verbatim", () => {
    for (const loadName of [
      "corning_96_wellplate_360ul_flat",
      "agilent_1_reservoir_290ml",
      "opentrons_96_tiprack_300ul",
    ]) {
      const entry = catalogEntryForDeclare(loadName);
      expect(entry, loadName).not.toBeNull();
      expect(entry?.declare).toBe(loadName);
      // Labware load_names must contain "_" — that's how the gateway
      // distinguishes a load_name from a legacy kind string.
      expect(loadName).toContain("_");
    }
  });

  it("module entries match the gateway's declaration keys exactly", () => {
    const moduleDeclares = OT2_CATALOG.filter((e) => e.category === "module").map(
      (e) => e.declare,
    );
    // MODULE_NAME_TO_KEY values are the gateway's _MODULE_KINDS keys.
    expect(new Set(moduleDeclares)).toEqual(new Set(Object.values(MODULE_NAME_TO_KEY)));
  });

  it("gives well grids to every plate/reservoir/tiprack entry", () => {
    for (const e of OT2_CATALOG) {
      if (e.category === "plate" || e.category === "reservoir" || e.category === "tiprack") {
        expect(e.rows, e.key).toBeGreaterThan(0);
        expect(e.columns, e.key).toBeGreaterThan(0);
      }
    }
  });
});

describe("groupedCatalog", () => {
  it("groups in category order with no empty groups", () => {
    const groups = groupedCatalog();
    const order = groups.map((g) => g.category);
    expect(order).toEqual(CATEGORY_ORDER.filter((c) => order.includes(c)));
    for (const g of groups) expect(g.entries.length).toBeGreaterThan(0);
  });

  it("searches across label, declare string, and category", () => {
    const byLoadName = groupedCatalog("tiprack_300").flatMap((g) => g.entries);
    expect(byLoadName.some((e) => e.key === "opentrons_96_tiprack_300ul")).toBe(true);

    const byLabel = groupedCatalog("heater").flatMap((g) => g.entries);
    expect(byLabel.some((e) => e.key === "heater_shaker_module")).toBe(true);

    const byCategory = groupedCatalog("modules").flatMap((g) => g.entries);
    expect(byCategory.every((e) => e.category === "module")).toBe(true);

    expect(groupedCatalog("no-such-thing-xyz")).toEqual([]);
  });
});
