import { describe, expect, it } from "vitest";

import {
  buildDefinition,
  defaultSpec,
  specFromDefinition,
  validateSpec,
  type LabwareSpec,
} from "./labware-schema";

function goodSpec(overrides: Partial<LabwareSpec> = {}): LabwareSpec {
  return {
    ...defaultSpec(),
    loadName: "matterlab_24_vialplate_2ml",
    displayName: "MatterLab 24 vial plate 2 mL",
    brand: "MatterLab",
    brandIds: "ML-24-2ML\nML-24-2ML-B",
    productLinks: "https://example.com/products/ml-24-2ml",
    rows: 4,
    columns: 6,
    spacingX: 18,
    spacingY: 18,
    offsetA1X: 18.38,
    offsetA1Y: 14.24,
    wellDiameter: 12,
    wellDepth: 12,
    footprintZ: 16,
    wellVolumeUl: 2000,
    ...overrides,
  };
}

describe("validateSpec", () => {
  it("accepts a well-formed spec", () => {
    expect(validateSpec(goodSpec())).toEqual([]);
  });

  it("enforces load-name rules (charset + underscore)", () => {
    expect(validateSpec(goodSpec({ loadName: "Bad Name" }))[0].field).toBe("loadName");
    const noUnderscore = validateSpec(goodSpec({ loadName: "vialplate" }));
    expect(noUnderscore.some((i) => i.message.includes("underscore"))).toBe(true);
  });

  it("enforces the OT-2 slot envelope", () => {
    const issues = validateSpec(goodSpec({ footprintX: 300 }));
    expect(issues.some((i) => i.field === "footprintX" && i.message.includes("128"))).toBe(true);
  });

  it("catches wells overhanging the footprint", () => {
    // 6 columns × 25 mm spacing from x=18 runs past a 127.76 mm footprint.
    const issues = validateSpec(goodSpec({ spacingX: 25 }));
    expect(issues.some((i) => i.field === "spacingX")).toBe(true);
  });

  it("requires depth ≤ height, tipLength for tip racks", () => {
    expect(
      validateSpec(goodSpec({ wellDepth: 50, footprintZ: 16 })).some(
        (i) => i.field === "wellDepth",
      ),
    ).toBe(true);
    expect(
      validateSpec(goodSpec({ displayCategory: "tipRack" })).some(
        (i) => i.field === "tipLength",
      ),
    ).toBe(true);
    expect(
      validateSpec(goodSpec({ displayCategory: "tipRack", tipLength: 59.3 })),
    ).toEqual([]);
  });

  it("rejects malformed and non-HTTP product links", () => {
    expect(
      validateSpec(goodSpec({ productLinks: "not a URL" })).some(
        (i) => i.field === "productLinks",
      ),
    ).toBe(true);
    expect(
      validateSpec(goodSpec({ productLinks: "ftp://example.com/plate" })).some(
        (i) => i.field === "productLinks",
      ),
    ).toBe(true);
  });
});

describe("buildDefinition", () => {
  it("expands the grid into schema-2 wells with correct coordinates", () => {
    const defn = buildDefinition(goodSpec()) as Record<string, any>;
    expect(defn.schemaVersion).toBe(2);
    expect(defn.parameters.loadName).toBe("matterlab_24_vialplate_2ml");
    expect(defn.brand).toEqual({
      brand: "MatterLab",
      brandId: ["ML-24-2ML", "ML-24-2ML-B"],
      links: ["https://example.com/products/ml-24-2ml"],
    });
    expect(Object.keys(defn.wells)).toHaveLength(24);
    // Column-major ordering: first column is A1..D1.
    expect(defn.ordering[0]).toEqual(["A1", "B1", "C1", "D1"]);
    // A1 at the back: y = footprintY - offsetA1Y.
    expect(defn.wells.A1.y).toBeCloseTo(85.48 - 14.24, 2);
    expect(defn.wells.A1.x).toBeCloseTo(18.38, 2);
    // Row B is one spacingY toward the front (lower y).
    expect(defn.wells.B1.y).toBeCloseTo(defn.wells.A1.y - 18, 2);
    // Well z = overall height - depth.
    expect(defn.wells.A1.z).toBeCloseTo(16 - 12, 2);
    expect(defn.groups[0].wells).toHaveLength(24);
  });

  it("marks tip racks and carries tipLength", () => {
    const defn = buildDefinition(
      goodSpec({ displayCategory: "tipRack", tipLength: 59.3 }),
    ) as Record<string, any>;
    expect(defn.parameters.isTiprack).toBe(true);
    expect(defn.parameters.tipLength).toBe(59.3);
  });

  it("specFromDefinition inverts buildDefinition (load-to-edit round trip)", () => {
    const original = goodSpec({ wellBottomShape: "v" });
    const { spec: loaded, warnings } = specFromDefinition(buildDefinition(original));
    expect(warnings).toEqual([]);
    expect(loaded).toEqual(original);
  });

  it("specFromDefinition round-trips a rectangular-well tip rack", () => {
    const original = goodSpec({
      displayCategory: "tipRack",
      tipLength: 59.3,
      wellShape: "rectangular",
      wellXDimension: 8,
      wellYDimension: 8,
      wellDiameter: undefined,
    });
    const { spec: loaded } = specFromDefinition(buildDefinition(original));
    expect(loaded).toEqual(original);
  });

  it("specFromDefinition flags non-uniform wells", () => {
    const defn = buildDefinition(goodSpec()) as Record<string, any>;
    defn.wells.A1.depth = 5; // one odd well
    const { warnings } = specFromDefinition(defn);
    expect(warnings.some((w) => w.includes("not uniform"))).toBe(true);
  });

  it("round-trips through the API-side validation rules", () => {
    // Mirror api/app/labware.py::validate_definition invariants.
    const defn = buildDefinition(goodSpec()) as Record<string, any>;
    const wells = defn.wells as Record<string, any>;
    const ordered = (defn.ordering as string[][]).flat();
    expect([...ordered].sort()).toEqual(Object.keys(wells).sort());
    for (const w of Object.values(wells)) {
      expect(w.x).toBeGreaterThanOrEqual(0);
      expect(w.x).toBeLessThanOrEqual(defn.dimensions.xDimension);
      expect(w.y).toBeGreaterThanOrEqual(0);
      expect(w.y).toBeLessThanOrEqual(defn.dimensions.yDimension);
      expect(w.depth).toBeLessThanOrEqual(defn.dimensions.zDimension);
    }
  });
});
