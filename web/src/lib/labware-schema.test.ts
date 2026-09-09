import { describe, expect, it } from "vitest";

import {
  buildDefinition,
  defaultSpec,
  gridFromSpec,
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
    expect(buildDefinition(loaded)).toEqual(buildDefinition(original));
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
    expect(buildDefinition(loaded)).toEqual(buildDefinition(original));
  });

  it("specFromDefinition preserves non-uniform wells", () => {
    const defn = buildDefinition(goodSpec()) as Record<string, any>;
    defn.wells.A1.depth = 5; // one odd well
    const { spec, warnings } = specFromDefinition(defn);
    expect(warnings).toEqual([]);
    expect(buildDefinition(spec)).toEqual(defn);
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

describe("independent well grids", () => {
  function mixedRack() {
    const base = goodSpec({ footprintZ: 124.35, footprintX: 127.75, footprintY: 85.5 });
    const small = { ...gridFromSpec(base), rows: 3, columns: 2, offsetA1X: 13.88,
      offsetA1Y: 17.75, spacingX: 25, spacingY: 25, wellDiameter: 14.7,
      wellDepth: 117.5, wellZ: 6.85, wellVolumeUl: 15000 };
    const large = { ...small, rows: 2, columns: 2, offsetA1X: 71.38, offsetA1Y: 25.25,
      spacingX: 35, spacingY: 35, wellDiameter: 27.81, wellDepth: 112.85, wellZ: 7.3, wellVolumeUl: 50000 };
    return { ...base, wellGroups: [
      { label: "15 mL", firstColumn: 1, grid: small },
      { label: "50 mL", firstColumn: 3, grid: large },
    ] };
  }
  it("builds 3+3+2+2 columns with independent sizes, positions and heights", () => {
    const spec = mixedRack();
    expect(validateSpec(spec)).toEqual([]);
    const d = buildDefinition(spec) as Record<string, any>;
    expect(d.ordering.map((c: string[]) => c.length)).toEqual([3, 3, 2, 2]);
    expect(d.wells.A1).toMatchObject({ x: 13.88, y: 67.75, diameter: 14.7, z: 6.85 });
    expect(d.wells.B4).toMatchObject({ x: 106.38, y: 25.25, diameter: 27.81, z: 7.3 });
    expect(d.wells.C3).toBeUndefined();
    expect(specFromDefinition(d).spec.wellGroups).toHaveLength(2);
    expect(buildDefinition(specFromDefinition(d).spec)).toEqual(d);
  });
  it("retains staggered coordinates, nonstandard IDs, ordering and internal sections exactly", () => {
    const d = buildDefinition(mixedRack()) as Record<string, any>;
    d.wells.B1.x += 0.123456;
    d.wells.B1.z = 4.123456;
    d.wells.B1.geometryDefinitionId = "cone";
    d.innerLabwareGeometry = { cone: { sections: [{ shape: "conical", bottomHeight: 0, topHeight: 100, bottomDiameter: 1, topDiameter: 14.7 }] } };
    d.wells.odd = d.wells.A4; delete d.wells.A4;
    d.ordering[3][0] = "odd";
    d.groups[1].wells[2] = "odd";
    d.groups[1].metadata.custom = "preserve";
    const loaded = specFromDefinition(d).spec;
    expect(buildDefinition(loaded)).toEqual(d);
    loaded.displayName = "Renamed rack";
    expect(buildDefinition(loaded)).toEqual({ ...d, metadata: { ...d.metadata, displayName: "Renamed rack" } });
  });
  it("edits only the selected grid and keeps rectangular wells rectangular", () => {
    const d = buildDefinition(mixedRack()) as Record<string, any>;
    const loaded = specFromDefinition(d).spec;
    loaded.wellGroups![1].grid = { ...loaded.wellGroups![1].grid, wellShape: "rectangular", wellXDimension: 20, wellYDimension: 15 };
    const edited = buildDefinition(loaded) as Record<string, any>;
    expect(edited.wells.A1).toEqual(d.wells.A1);
    expect(edited.wells.A3).toMatchObject({ shape: "rectangular", xDimension: 20, yDimension: 15 });
    expect(edited.wells.A3.diameter).toBeUndefined();
  });
  it("rejects duplicate IDs, invalid spacing, nonfinite offsets and empty groups", () => {
    const spec = mixedRack();
    spec.wellGroups[1].firstColumn = 1;
    expect(validateSpec(spec).some(i => i.message.includes("Duplicate"))).toBe(true);
    expect(() => buildDefinition(spec)).toThrow("Duplicate");
    spec.wellGroups[1].firstColumn = 3;
    spec.wellGroups[1].grid.offsetA1X = NaN;
    expect(validateSpec(spec).some(i => i.message.includes("finite"))).toBe(true);
    expect(validateSpec({ ...spec, wellGroups: [] })).not.toEqual([]);
  });
});

it("preserves imported categories, tip parameters and vendor metadata", () => {
  const d = buildDefinition(goodSpec()) as Record<string, any>;
  d.metadata.displayCategory = "aluminumBlock";
  d.parameters.tipLength = 51;
  d.brand.extra = "source field";
  expect(buildDefinition(specFromDefinition(d).spec)).toEqual(d);
});
it("rejects physically overlapping independent grids", () => {
  const spec = goodSpec();
  const grid = { ...gridFromSpec(spec), rows: 1, columns: 1 };
  expect(validateSpec({ ...spec, wellGroups: [
    { label: "one", firstColumn: 1, grid }, { label: "two", firstColumn: 2, grid },
  ] }).some(i => i.message.includes("overlap"))).toBe(true);
});
