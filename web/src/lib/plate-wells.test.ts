/**
 * Pure-logic tests for the selected-plate inspector's per-well model and the
 * geometry reader behind its side elevation.
 *
 * These two modules are duplicated verbatim in the gateway's own UI
 * (`opentrons-server/ui/src/lib/`), which has no test runner; this repo has
 * vitest, so the shared logic is pinned here for both.
 */

import { describe, expect, it } from "vitest";

import {
  geometryFromDefinition,
  wellHalfX,
  wellHalfY,
  type LabwareGeometry,
} from "./labware-geometry";
import { buildWellModel, columnKinds, wellOrder } from "./plate-wells";
import type { TipRackSummary } from "./ot2-deck";

// --- fixtures ---------------------------------------------------------------

const COLUMN_1 = ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1"];

function tipRack(tips: Record<string, string>, nickname = "tips_20"): TipRackSummary {
  const nonFresh = Object.keys(tips).length;
  return {
    nickname,
    total: 96,
    available: 96 - nonFresh,
    empty: Object.values(tips).filter((s) => s === "empty").length,
    touched: Object.values(tips).filter((s) => s !== "empty").length,
    tips,
  };
}

/** A minimal but realistically-shaped 2×3 definition (mm). */
function definition(overrides: Record<string, unknown> = {}) {
  const wells: Record<string, unknown> = {};
  const ids = [
    ["A1", "B1"],
    ["A2", "B2"],
    ["A3", "B3"],
  ];
  ids.forEach((column, c) =>
    column.forEach((id, r) => {
      wells[id] = {
        x: 10 + c * 9,
        y: 20 - r * 9,
        z: 3,
        depth: 10,
        shape: "circular",
        diameter: 6,
        totalLiquidVolume: 360,
      };
    }),
  );
  return {
    ordering: ids,
    wells,
    dimensions: { xDimension: 127.76, yDimension: 85.47, zDimension: 14.22 },
    parameters: { loadName: "test_6_wellplate", isTiprack: false },
    metadata: { displayName: "Test 6 Well Plate" },
    ...overrides,
  };
}

// --- geometry ---------------------------------------------------------------

describe("geometryFromDefinition", () => {
  it("reads footprint, grid, wells and volume", () => {
    const g = geometryFromDefinition(definition()) as LabwareGeometry;
    expect(g).not.toBeNull();
    expect(g.footprintX).toBeCloseTo(127.76);
    expect(g.footprintZ).toBeCloseTo(14.22);
    // ordering is column-major: 3 columns of 2 rows.
    expect(g.columns).toBe(3);
    expect(g.rows).toBe(2);
    expect(g.ordering[0]).toEqual(["A1", "B1"]);
    expect(g.wells.A1.depth).toBe(10);
    expect(g.wellVolumeUl).toBe(360);
    expect(g.isTiprack).toBe(false);
    expect(g.tipLength).toBeNull();
  });

  it("carries tip length for a rack", () => {
    const g = geometryFromDefinition(
      definition({ parameters: { loadName: "rack", isTiprack: true, tipLength: 39.2 } }),
    );
    expect(g?.isTiprack).toBe(true);
    expect(g?.tipLength).toBe(39.2);
  });

  it("returns null rather than inventing dimensions", () => {
    // The elevation is to scale, so a definition missing the numbers it scales
    // against must produce no drawing at all.
    expect(geometryFromDefinition(null)).toBeNull();
    expect(geometryFromDefinition({})).toBeNull();
    expect(geometryFromDefinition(definition({ dimensions: {} }))).toBeNull();
    expect(geometryFromDefinition(definition({ wells: {} }))).toBeNull();
    expect(geometryFromDefinition(definition({ ordering: [] }))).toBeNull();
  });

  it("skips malformed wells but keeps the usable ones", () => {
    const defn = definition();
    (defn.wells as Record<string, unknown>).B3 = { shape: "circular" }; // no x/y/depth
    const g = geometryFromDefinition(defn);
    expect(g?.wells.B3).toBeUndefined();
    expect(g?.wells.A1).toBeDefined();
  });

  it("measures circular and rectangular wells on the right axis", () => {
    expect(wellHalfX({ x: 0, y: 0, z: 0, depth: 1, shape: "circular", diameter: 6 })).toBe(3);
    const rect = {
      x: 0,
      y: 0,
      z: 0,
      depth: 1,
      shape: "rectangular" as const,
      xDimension: 8,
      yDimension: 70,
    };
    expect(wellHalfX(rect)).toBe(4);
    expect(wellHalfY(rect)).toBe(35);
  });
});

// --- well ordering ----------------------------------------------------------

describe("wellOrder", () => {
  it("prefers the definition's own ordering", () => {
    const g = geometryFromDefinition(definition()) as LabwareGeometry;
    expect(wellOrder(g, 0, 0).map((w) => w.well)).toEqual(["A1", "B1", "A2", "B2", "A3", "B3"]);
  });

  it("synthesizes a column-major grid when there is no definition", () => {
    const order = wellOrder(null, 8, 12);
    expect(order).toHaveLength(96);
    expect(order[0]).toEqual({ well: "A1", row: 0, column: 0 });
    expect(order[7]).toEqual({ well: "H1", row: 7, column: 0 });
    expect(order[8].well).toBe("A2");
  });
});

// --- tip racks --------------------------------------------------------------

describe("buildWellModel — tip racks", () => {
  const base = {
    isTiprack: true,
    rows: 8,
    columns: 12,
    geometry: null,
    samples: null,
    nickname: "tips_20",
  };

  it("treats wells absent from the tracker's map as fresh", () => {
    const model = buildWellModel({ ...base, tipRack: tipRack({}) });
    expect(model.total).toBe(96);
    expect(model.counts.fresh).toBe(96);
    expect(model.tracked).toBe(true);
  });

  it("renders the column an 8-channel pick consumed", () => {
    const tips = Object.fromEntries(COLUMN_1.map((w) => [w, "empty"]));
    const model = buildWellModel({ ...base, tipRack: tipRack({ ...tips, H2: "plate_D_B2" }) });
    expect(model.counts.empty).toBe(8);
    expect(model.counts.touched).toBe(1);
    expect(model.counts.fresh).toBe(87);
    expect(model.byWell.A1.kind).toBe("empty");
    expect(model.byWell.H2).toMatchObject({ kind: "touched", detail: "plate_D_B2" });
  });

  it("is unknown — not full — for a rack the tracker has never registered", () => {
    // The distinction the whole component exists to preserve.
    const model = buildWellModel({ ...base, tipRack: null });
    expect(model.tracked).toBe(false);
    expect(model.counts.unknown).toBe(96);
    expect(model.counts.fresh).toBeUndefined();
  });

  it("rings every well of a mounted multi-channel span", () => {
    const model = buildWellModel({
      ...base,
      tipRack: tipRack({}),
      mountedTips: [{ pipette: "p20", rack: "tips_20", well: "A3", wells: COLUMN_1.map((w) => w.replace("1", "3")) }],
    });
    expect(model.cells.filter((c) => c.mounted)).toHaveLength(8);
    expect(model.byWell.H3.mounted).toBe(true);
    expect(model.byWell.H4.mounted).toBeUndefined();
  });

  it("falls back to the addressed well for a pre-multi-channel gateway", () => {
    const model = buildWellModel({
      ...base,
      tipRack: tipRack({}),
      mountedTips: [{ pipette: "p300", rack: "tips_20", well: "B5" }],
    });
    expect(model.cells.filter((c) => c.mounted)).toHaveLength(1);
    expect(model.byWell.B5.mounted).toBe(true);
  });

  it("ignores mounted tips belonging to a different rack", () => {
    const model = buildWellModel({
      ...base,
      tipRack: tipRack({}),
      mountedTips: [{ pipette: "p300", rack: "tips_300", well: "A1" }],
    });
    expect(model.cells.some((c) => c.mounted)).toBe(false);
  });
});

// --- plates -----------------------------------------------------------------

describe("buildWellModel — plates", () => {
  const base = {
    isTiprack: false,
    rows: 8,
    columns: 12,
    geometry: null,
    tipRack: null,
    nickname: "plate_D",
  };

  it("marks tracked samples and leaves the rest vacant", () => {
    const model = buildWellModel({
      ...base,
      samples: [
        { well: "A1", sample_id: "caffeine-001", volume_ul: 180 },
        { well: "B2", sample_id: "ibuprofen-002", volume_ul: 120 },
      ],
    });
    expect(model.contents).toBe("plate");
    expect(model.counts.sample).toBe(2);
    expect(model.counts.vacant).toBe(94);
    expect(model.byWell.A1).toMatchObject({ detail: "caffeine-001", volumeUl: 180 });
  });

  it("is untracked when the slot carries no wells at all", () => {
    const model = buildWellModel({ ...base, samples: null });
    expect(model.tracked).toBe(false);
    expect(model.counts.unknown).toBe(96);
  });

  it("tolerates a sample with no id", () => {
    const model = buildWellModel({ ...base, samples: [{ well: "C3" }] });
    expect(model.byWell.C3.kind).toBe("sample");
    expect(model.byWell.C3.detail).toBeUndefined();
  });
});

// --- column aggregation (the elevation's unit) ------------------------------

describe("columnKinds", () => {
  const base = { isTiprack: true, rows: 8, columns: 12, geometry: null, samples: null };

  it("keeps a part-consumed column distinct from fresh and empty", () => {
    // A multi-channel head cannot pick from a partial column, so "mixed" must
    // not be rounded to either neighbour.
    const tips: Record<string, string> = Object.fromEntries(COLUMN_1.map((w) => [w, "empty"]));
    tips.H2 = "empty";
    const model = buildWellModel({ ...base, tipRack: tipRack(tips) });
    const kinds = columnKinds(model);
    expect(kinds[0]).toBe("empty"); // whole column gone
    expect(kinds[1]).toBe("mixed"); // only H2 gone
    expect(kinds[2]).toBe("fresh"); // untouched
  });

  it("reports every column unknown for an unregistered rack", () => {
    const model = buildWellModel({ ...base, tipRack: null });
    expect(columnKinds(model)).toEqual(Array(12).fill("unknown"));
  });
});
