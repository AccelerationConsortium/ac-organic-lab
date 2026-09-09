/**
 * Opentrons labware schema-2 builder + validator (pure logic, no React).
 *
 * A TypeScript port of the rules in `opentrons-server`'s `LabwareGenerator`
 * and the dashboard API's `validate_definition` (api/app/labware.py): a
 * parametric spec (grid, spacing, offsets, well geometry) is expanded into a
 * complete schema-2 definition JSON that the OT-2 can load via
 * `protocol.load_labware_from_definition`.
 *
 * Coordinate conventions (Opentrons schema 2): origin is the footprint's
 * front-left-bottom corner; x grows left→right, y grows front→back, z up.
 * Row A is the BACK row (highest y), column 1 the leftmost. The form asks
 * for A1's offset from the left edge (x) and from the BACK edge (y, "top"
 * when looking down at the deck) — the builder converts to schema coords.
 */

// OT-2 slot envelope (mm): the ANSI/SLAS footprint (127.76 × 85.48) plus
// clearance. Keep in sync with api/app/labware.py.
export const MAX_DIMENSIONS = { x: 128, y: 86, z: 200 } as const;

export const LOAD_NAME_RE = /^[a-z0-9._]+$/;

export type WellShape = "circular" | "rectangular";
export type DisplayCategory = "wellPlate" | "reservoir" | "tipRack" | "tubeRack" | "adapter" | "aluminumBlock" | "lid" | "other" | "system" | "trash";

export interface LabwareSpec {
  /** Independent grids; imported groups retain exact well data until edited. */
  wellGroups?: WellGrid[];
  sourceDefinition?: Record<string, unknown>;
  loadName: string;
  displayName: string;
  /** Vendor or manufacturer; emitted as Opentrons `brand.brand`. */
  brand: string;
  /** One OEM part/product number per line; emitted as `brand.brandId[]`. */
  brandIds: string;
  /** One manufacturer product URL per line; emitted as `brand.links[]`. */
  productLinks: string;
  displayCategory: DisplayCategory;
  rows: number;
  columns: number;
  /** Overall footprint (mm). ANSI/SLAS plate is 127.76 × 85.48. */
  footprintX: number;
  footprintY: number;
  /** Overall height incl. lid-less top of wells (mm). */
  footprintZ: number;
  /** A1 well-center offset from the LEFT footprint edge (mm). */
  offsetA1X: number;
  /** A1 well-center offset from the BACK footprint edge (mm). */
  offsetA1Y: number;
  /** Center-to-center spacing (mm). */
  spacingX: number;
  spacingY: number;
  wellShape: WellShape;
  /** For circular wells. */
  wellDiameter?: number;
  /** For rectangular wells. */
  wellXDimension?: number;
  wellYDimension?: number;
  wellDepth: number;
  /** Max volume per well (µL). */
  wellVolumeUl: number;
  wellBottomShape: "flat" | "u" | "v";
  /** Required when displayCategory is tipRack (mm). */
  tipLength?: number;
}

export type GridSpec = Pick<LabwareSpec,
  "rows" | "columns" | "offsetA1X" | "offsetA1Y" | "spacingX" | "spacingY" |
  "wellShape" | "wellDiameter" | "wellXDimension" | "wellYDimension" |
  "wellDepth" | "wellVolumeUl" | "wellBottomShape"> & { wellZ: number };
export interface WellGrid {
  label: string;
  firstColumn: number;
  grid: GridSpec;
  /** Preserve nonstandard IDs and source precision for imported grids. */
  ordering?: string[][];
  originalGrid?: GridSpec;
  originalWells?: Record<string, Record<string, unknown>>;
}
export function gridFromSpec(spec: LabwareSpec): GridSpec {
  const { rows, columns, offsetA1X, offsetA1Y, spacingX, spacingY, wellShape,
    wellDiameter, wellXDimension, wellYDimension, wellDepth, wellVolumeUl, wellBottomShape } = spec;
  return { rows, columns, offsetA1X, offsetA1Y, spacingX, spacingY, wellShape,
    wellDiameter, wellXDimension, wellYDimension, wellDepth, wellVolumeUl, wellBottomShape,
    wellZ: round2(spec.footprintZ - wellDepth) };
}
function gridDefinition(spec: LabwareSpec, group: WellGrid) {
  const g = group.grid;
  if (group.originalGrid && JSON.stringify(g) === JSON.stringify(group.originalGrid)) {
    return { wells: structuredClone(group.originalWells!), ordering: group.ordering! };
  }
  const generated = buildDefinition({ ...spec, ...g, wellGroups: undefined,
    sourceDefinition: undefined }) as { wells: Record<string, Record<string, unknown>>; ordering: string[][] };
  const wells: Record<string, Record<string, unknown>> = {};
  const sameCounts = group.ordering?.length === g.columns && group.ordering.every(c => c.length === g.rows);
  const ordering = generated.ordering.map((col, c) => col.map((name, r) => {
    const id = sameCounts ? group.ordering![c][r] : `${rowName(r)}${group.firstColumn + c}`;
    wells[id] = { ...generated.wells[name], z: g.wellZ };
    return id;
  }));
  return { wells, ordering };
}

/** A sensible starting spec (96-well SLAS plate geometry). */
export function defaultSpec(): LabwareSpec {
  return {
    loadName: "",
    displayName: "",
    brand: "",
    brandIds: "",
    productLinks: "",
    displayCategory: "wellPlate",
    rows: 8,
    columns: 12,
    footprintX: 127.76,
    footprintY: 85.48,
    footprintZ: 14.2,
    offsetA1X: 14.38,
    offsetA1Y: 11.24,
    spacingX: 9,
    spacingY: 9,
    wellShape: "circular",
    wellDiameter: 6.86,
    wellDepth: 10.7,
    wellVolumeUl: 360,
    wellBottomShape: "flat",
  };
}

function rowName(i: number): string {
  // A..Z, then AA.. (plates beyond 26 rows don't exist, but don't crash).
  let name = "";
  let n = i;
  do {
    name = String.fromCharCode(65 + (n % 26)) + name;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return name;
}

export interface ValidationIssue {
  field: keyof LabwareSpec | "general";
  message: string;
}

/** Validate the parametric spec. Empty result == buildable. */
export function validateSpec(spec: LabwareSpec): ValidationIssue[] {
  if (spec.wellGroups) {
    const issues: ValidationIssue[] = [];
    if (!spec.wellGroups.length) return [{ field: "wellGroups", message: "Add at least one well grid." }];
    const ids = new Set<string>();
    for (const group of spec.wellGroups) {
      const g = group.grid;
      const groupIssues = validateSpec({ ...spec, ...g, wellGroups: undefined });
      issues.push(...groupIssues.map(i => ({ ...i, message: `${group.label}: ${i.message}` })));
      if (!Number.isInteger(group.firstColumn) || group.firstColumn < 1)
        issues.push({ field: "wellGroups", message: `${group.label}: first column must be a positive integer.` });
      if (!Number.isFinite(g.wellZ) || g.wellZ < 0)
        issues.push({ field: "wellGroups", message: `${group.label}: well bottom Z must be finite and nonnegative.` });
      if (groupIssues.length === 0) {
        for (const id of gridDefinition(spec, group).ordering.flat()) {
          if (ids.has(id)) issues.push({ field: "wellGroups", message: `Duplicate well ID: ${id}. Change the first column.` });
          ids.add(id);
        }
      }
    }
    if (!issues.length) {
      const entries = spec.wellGroups.flatMap(group => Object.entries(gridDefinition(spec, group).wells));
      for (let i = 0; i < entries.length; i++) for (let j = i + 1; j < entries.length; j++) {
        const [aName, a] = entries[i], [bName, b] = entries[j];
        const half = (w: Record<string, unknown>, axis: "x" | "y") => Number(w.shape === "circular" ? w.diameter : w[`${axis}Dimension`]) / 2;
        const dx = Math.abs(Number(a.x) - Number(b.x)), dy = Math.abs(Number(a.y) - Number(b.y));
        let overlap: boolean;
        if (a.shape === "circular" && b.shape === "circular") overlap = Math.hypot(dx, dy) < half(a, "x") + half(b, "x") - 1e-7;
        else if (a.shape === "rectangular" && b.shape === "rectangular") overlap = dx < half(a, "x") + half(b, "x") - 1e-7 && dy < half(a, "y") + half(b, "y") - 1e-7;
        else {
          const circle = a.shape === "circular" ? a : b, rect = a.shape === "rectangular" ? a : b;
          overlap = Math.hypot(Math.max(0, dx - half(rect, "x")), Math.max(0, dy - half(rect, "y"))) < half(circle, "x") - 1e-7;
        }
        if (overlap) {
          issues.push({ field: "wellGroups", message: `Wells ${aName} and ${bName} overlap. Adjust offsets, spacing or well sizes.` });
          return issues;
        }
      }
    }
    return issues;
  }
  const issues: ValidationIssue[] = [];
  const err = (field: ValidationIssue["field"], message: string) =>
    issues.push({ field, message });

  if (!LOAD_NAME_RE.test(spec.loadName)) {
    err("loadName", "Lowercase letters, digits, dot and underscore only.");
  } else if (!spec.loadName.includes("_")) {
    err("loadName", "Must contain at least one underscore (e.g. brand_24_vialplate_2ml).");
  }
  if (!spec.displayName.trim()) err("displayName", "Display name is required.");
  if (!spec.brand.trim()) err("brand", "Vendor / manufacturer is required (e.g. Corning).");
  for (const link of lines(spec.productLinks)) {
    try {
      const url = new URL(link);
      if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error();
    } catch {
      err("productLinks", `Not a valid HTTP(S) URL: ${link}`);
    }
  }

  if (!Number.isInteger(spec.rows) || spec.rows < 1) err("rows", "At least 1 row.");
  if (!Number.isInteger(spec.columns) || spec.columns < 1) err("columns", "At least 1 column.");

  for (const [field, value, max] of [
    ["footprintX", spec.footprintX, MAX_DIMENSIONS.x],
    ["footprintY", spec.footprintY, MAX_DIMENSIONS.y],
    ["footprintZ", spec.footprintZ, MAX_DIMENSIONS.z],
  ] as const) {
    if (!Number.isFinite(value) || !(value > 0)) err(field, "Must be finite and positive.");
    else if (value > max) err(field, `Exceeds the OT-2 slot limit (${max} mm).`);
  }

  if (spec.wellShape === "circular") {
    if (!(spec.wellDiameter && Number.isFinite(spec.wellDiameter) && spec.wellDiameter > 0))
      err("wellDiameter", "Diameter is required for circular wells.");
  } else {
    if (!(spec.wellXDimension && Number.isFinite(spec.wellXDimension) && spec.wellXDimension > 0))
      err("wellXDimension", "X size is required for rectangular wells.");
    if (!(spec.wellYDimension && Number.isFinite(spec.wellYDimension) && spec.wellYDimension > 0))
      err("wellYDimension", "Y size is required for rectangular wells.");
  }
  if (!(spec.wellDepth > 0)) err("wellDepth", "Well depth must be positive.");
  else if (spec.wellDepth > spec.footprintZ)
    err("wellDepth", "Well depth cannot exceed the overall height.");
  if (!(spec.wellVolumeUl > 0)) err("wellVolumeUl", "Volume must be positive.");
  if (spec.displayCategory === "tipRack" && !(spec.tipLength && spec.tipLength > 0))
    err("tipLength", "Tip length is required for tip racks.");

  for (const field of ["offsetA1X", "offsetA1Y", "spacingX", "spacingY", "wellDepth", "wellVolumeUl"] as const) {
    if (!Number.isFinite(spec[field])) err(field, "Must be a finite number.");
  }
  if (spec.columns > 1 && !(spec.spacingX > 0)) err("spacingX", "Spacing must be positive.");
  if (spec.rows > 1 && !(spec.spacingY > 0)) err("spacingY", "Spacing must be positive.");
  if (spec.rows * spec.columns > 1536) err("rows", "At most 1536 wells per grid.");

  // Geometry: every well center ± half its width must stay inside the footprint.
  const halfX =
    spec.wellShape === "circular" ? (spec.wellDiameter ?? 0) / 2 : (spec.wellXDimension ?? 0) / 2;
  const halfY =
    spec.wellShape === "circular" ? (spec.wellDiameter ?? 0) / 2 : (spec.wellYDimension ?? 0) / 2;
  const lastColX = spec.offsetA1X + (spec.columns - 1) * spec.spacingX;
  const lastRowYFromBack = spec.offsetA1Y + (spec.rows - 1) * spec.spacingY;
  if (spec.offsetA1X - halfX < 0)
    err("offsetA1X", "A1 overhangs the left edge (offset < well half-width).");
  if (lastColX + halfX > spec.footprintX)
    err("spacingX", "The last column overhangs the right edge.");
  if (spec.offsetA1Y - halfY < 0)
    err("offsetA1Y", "Row A overhangs the back edge (offset < well half-height).");
  if (lastRowYFromBack + halfY > spec.footprintY)
    err("spacingY", "The last row overhangs the front edge.");

  return issues;
}

/** Expand a valid spec into a complete Opentrons schema-2 definition. */
export function buildDefinition(spec: LabwareSpec): Record<string, unknown> {
  if (spec.wellGroups) {
    const base = spec.sourceDefinition ? structuredClone(spec.sourceDefinition) :
      buildDefinition({ ...spec, wellGroups: undefined, sourceDefinition: undefined });
    const wells: Record<string, Record<string, unknown>> = {};
    const ordering: string[][] = [];
    for (const group of spec.wellGroups) {
      const built = gridDefinition(spec, group);
      for (const id of Object.keys(built.wells)) {
        if (id in wells) throw new Error(`Duplicate well ID: ${id}`);
      }
      Object.assign(wells, built.wells);
      ordering.push(...built.ordering);
    }
    const source = spec.sourceDefinition;
    // Keep access ordering and metadata untouched when membership is unchanged.
    const oldOrdering = source?.ordering as string[][] | undefined;
    const sameIds = oldOrdering && oldOrdering.flat().length === Object.keys(wells).length &&
      oldOrdering.flat().every(id => id in wells);
    const sameBottoms = spec.wellGroups.every(g => g.originalGrid?.wellBottomShape === g.grid.wellBottomShape);
    const parameters = { ...(base.parameters as Record<string, unknown>), loadName: spec.loadName,
      isTiprack: spec.displayCategory === "tipRack" };
    if (spec.displayCategory === "tipRack") Object.assign(parameters, { tipLength: spec.tipLength });
    else if (spec.sourceDefinition && spec.displayCategory === (spec.sourceDefinition.metadata as Record<string, unknown>)?.displayCategory) {
      if (spec.tipLength !== undefined) Object.assign(parameters, { tipLength: spec.tipLength });
    } else delete (parameters as Record<string, unknown>).tipLength;
    return { ...base,
      namespace: source && (source.parameters as Record<string, unknown>)?.loadName === spec.loadName ? base.namespace : "custom",
      metadata: { ...(base.metadata as object), displayName: spec.displayName, displayCategory: spec.displayCategory },
      brand: { ...(base.brand as object), brand: spec.brand.trim(),
        ...(spec.brandIds || (base.brand as Record<string, unknown>)?.brandId ? { brandId: lines(spec.brandIds) } : {}),
        ...(spec.productLinks || (base.brand as Record<string, unknown>)?.links ? { links: lines(spec.productLinks) } : {}) },
      parameters,
      dimensions: { xDimension: spec.footprintX, yDimension: spec.footprintY, zDimension: spec.footprintZ },
      wells, ordering: sameIds ? oldOrdering : ordering,
      groups: sameIds && sameBottoms ? base.groups : spec.wellGroups.map(group => ({
        wells: gridDefinition(spec, group).ordering.flat(), metadata: { wellBottomShape: group.grid.wellBottomShape },
      })),
    };
  }
  const wells: Record<string, Record<string, unknown>> = {};
  const ordering: string[][] = [];
  for (let col = 0; col < spec.columns; col++) {
    const colNames: string[] = [];
    for (let row = 0; row < spec.rows; row++) {
      const name = `${rowName(row)}${col + 1}`;
      const shape =
        spec.wellShape === "circular"
          ? { shape: "circular", diameter: spec.wellDiameter }
          : {
              shape: "rectangular",
              xDimension: spec.wellXDimension,
              yDimension: spec.wellYDimension,
            };
      wells[name] = {
        depth: spec.wellDepth,
        totalLiquidVolume: spec.wellVolumeUl,
        ...shape,
        x: round2(spec.offsetA1X + col * spec.spacingX),
        // Schema y grows front→back; row A sits at the BACK (highest y).
        y: round2(spec.footprintY - spec.offsetA1Y - row * spec.spacingY),
        z: round2(spec.footprintZ - spec.wellDepth),
      };
      colNames.push(name);
    }
    ordering.push(colNames);
  }

  return {
    schemaVersion: 2,
    version: 1,
    namespace: "custom",
    metadata: {
      displayName: spec.displayName,
      displayCategory: spec.displayCategory,
      displayVolumeUnits: "µL",
      tags: [],
    },
    brand: {
      brand: spec.brand.trim(),
      brandId: lines(spec.brandIds),
      links: lines(spec.productLinks),
    },
    parameters: {
      format: "irregular",
      isTiprack: spec.displayCategory === "tipRack",
      ...(spec.displayCategory === "tipRack" ? { tipLength: spec.tipLength } : {}),
      isMagneticModuleCompatible: false,
      loadName: spec.loadName,
      quirks: [],
    },
    dimensions: {
      xDimension: spec.footprintX,
      yDimension: spec.footprintY,
      zDimension: spec.footprintZ,
    },
    cornerOffsetFromSlot: { x: 0, y: 0, z: 0 },
    wells,
    ordering,
    groups: [
      {
        wells: ordering.flat(),
        metadata: { wellBottomShape: spec.wellBottomShape },
      },
    ],
  };
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

/** Parse the builder's one-value-per-line metadata fields. */
function lines(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

/** Read the common metadata and initial grid fields; explicit wells are imported below. */
function uniformSpecFromDefinition(defn: Record<string, unknown>): {
  spec: LabwareSpec;
  warnings: string[];
} {
  const warnings: string[] = [];
  const d = defn as {
    metadata?: { displayName?: string; displayCategory?: string };
    brand?: { brand?: string; brandId?: string[]; links?: string[] };
    parameters?: { loadName?: string; isTiprack?: boolean; tipLength?: number };
    dimensions?: { xDimension?: number; yDimension?: number; zDimension?: number };
    ordering?: string[][];
    wells?: Record<
      string,
      {
        x?: number;
        y?: number;
        depth?: number;
        totalLiquidVolume?: number;
        shape?: string;
        diameter?: number;
        xDimension?: number;
        yDimension?: number;
      }
    >;
    groups?: { metadata?: { wellBottomShape?: string } }[];
  };

  const spec = defaultSpec();
  spec.loadName = d.parameters?.loadName ?? "";
  spec.displayName = d.metadata?.displayName ?? "";
  spec.brand = d.brand?.brand ?? "";
  spec.brandIds = Array.isArray(d.brand?.brandId) ? d.brand.brandId.join("\n") : "";
  spec.productLinks = Array.isArray(d.brand?.links) ? d.brand.links.join("\n") : "";
  const category = d.metadata?.displayCategory;
  if (
    category === "wellPlate" ||
    category === "reservoir" ||
    category === "tipRack" ||
    category === "tubeRack" || category === "adapter" || category === "aluminumBlock" ||
    category === "lid" || category === "other" || category === "system" || category === "trash"
  ) {
    spec.displayCategory = category;
  } else if (d.parameters?.isTiprack) {
    spec.displayCategory = "tipRack";
  }
  if (typeof d.parameters?.tipLength === "number") spec.tipLength = d.parameters.tipLength;

  spec.footprintX = d.dimensions?.xDimension ?? spec.footprintX;
  spec.footprintY = d.dimensions?.yDimension ?? spec.footprintY;
  spec.footprintZ = d.dimensions?.zDimension ?? spec.footprintZ;

  const ordering = Array.isArray(d.ordering) ? d.ordering : [];
  spec.columns = ordering.length || 1;
  spec.rows = ordering[0]?.length || 1;

  const wells = d.wells ?? {};
  const a1 = wells["A1"];
  if (!a1) {
    warnings.push("Definition has no A1 well — geometry fields kept at defaults.");
    return { spec, warnings };
  }

  if (a1.shape === "rectangular") {
    spec.wellShape = "rectangular";
    spec.wellXDimension = a1.xDimension ?? undefined;
    spec.wellYDimension = a1.yDimension ?? undefined;
    spec.wellDiameter = undefined;
  } else {
    spec.wellShape = "circular";
    spec.wellDiameter = a1.diameter ?? spec.wellDiameter;
  }
  if (typeof a1.depth === "number") spec.wellDepth = a1.depth;
  if (typeof a1.totalLiquidVolume === "number") spec.wellVolumeUl = a1.totalLiquidVolume;
  if (typeof a1.x === "number") spec.offsetA1X = round2(a1.x);
  if (typeof a1.y === "number") spec.offsetA1Y = round2(spec.footprintY - a1.y);

  // Spacing from A1's neighbours (fall back to defaults for 1×N / N×1).
  const a2 = wells[ordering[1]?.[0] ?? "A2"];
  if (spec.columns > 1 && typeof a2?.x === "number" && typeof a1.x === "number") {
    spec.spacingX = round2(a2.x - a1.x);
  }
  const b1 = wells[ordering[0]?.[1] ?? "B1"];
  if (spec.rows > 1 && typeof b1?.y === "number" && typeof a1.y === "number") {
    spec.spacingY = round2(a1.y - b1.y);
  }

  const bottom = d.groups?.[0]?.metadata?.wellBottomShape;
  if (bottom === "flat" || bottom === "u" || bottom === "v") spec.wellBottomShape = bottom;

  return { spec, warnings };
}

/** Import explicit wells without resampling coordinates or discarding inner geometry.
 * Wells with matching geometry form a grid only when their positions are regular.
 * Otherwise each well remains an independently positioned one-well grid. */
export function specFromDefinition(defn: Record<string, unknown>): { spec: LabwareSpec; warnings: string[] } {
  const { spec } = uniformSpecFromDefinition(defn);
  const wells = defn.wells as Record<string, Record<string, unknown>>;
  const ordering = defn.ordering as string[][];
  if (!wells || !Array.isArray(ordering) || !ordering.length || ordering.some(c => !Array.isArray(c) || !c.length))
    throw new Error("Definition needs wells and nonempty ordering columns.");
  const ids = ordering.flat();
  if (new Set(ids).size !== ids.length || ids.length !== Object.keys(wells).length || ids.some(id => !wells[id]))
    throw new Error("Ordering must reference every well exactly once.");
  const buckets = new Map<string, string[]>();
  const groups = defn.groups as { wells: string[]; metadata?: { wellBottomShape?: string } }[] | undefined;
  const bottom = (id: string) => groups?.find(g => g.wells.includes(id))?.metadata?.wellBottomShape ?? "flat";
  for (const id of ids) {
    const { x, y, ...geometry } = wells[id];
    const key = JSON.stringify([Object.entries(geometry).sort(([a], [b]) => a.localeCompare(b)), bottom(id)]);
    buckets.set(key, [...(buckets.get(key) ?? []), id]);
  }
  const wellGroups: WellGrid[] = [];
  function add(names: string[][]) {
    const w = wells[names[0][0]];
    const g: GridSpec = {
      ...gridFromSpec(spec), rows: names[0].length, columns: names.length,
      offsetA1X: Number(w.x), offsetA1Y: spec.footprintY - Number(w.y),
      spacingX: names.length > 1 ? Number(wells[names[1][0]].x) - Number(w.x) : 0,
      spacingY: names[0].length > 1 ? Number(w.y) - Number(wells[names[0][1]].y) : 0,
      wellShape: w.shape as WellShape, wellDiameter: w.diameter as number | undefined,
      wellXDimension: w.xDimension as number | undefined, wellYDimension: w.yDimension as number | undefined,
      wellDepth: Number(w.depth), wellVolumeUl: Number(w.totalLiquidVolume), wellZ: Number(w.z),
      wellBottomShape: bottom(names[0][0]) as GridSpec["wellBottomShape"],
    };
    const regular = names.every((col, c) => col.length === g.rows && col.every((id, r) =>
      Math.abs(Number(wells[id].x) - g.offsetA1X - c * g.spacingX) < 1e-7 &&
      Math.abs(Number(wells[id].y) - (spec.footprintY - g.offsetA1Y - r * g.spacingY)) < 1e-7));
    if (!regular) { for (const id of names.flat()) add([[id]]); return; }
    wellGroups.push({ label: `Grid ${wellGroups.length + 1} (${names.flat().join(", ")})`,
      firstColumn: Number(names[0][0].match(/\d+$/)?.[0] ?? wellGroups.length + 1), grid: g,
      originalGrid: structuredClone(g), ordering: names,
      originalWells: Object.fromEntries(names.flat().map(id => [id, structuredClone(wells[id])])) });
  }
  for (const names of buckets.values()) {
    const xs = [...new Set(names.map(id => Number(wells[id].x)))].sort((a, b) => a - b);
    add(xs.map(x => names.filter(id => Number(wells[id].x) === x).sort((a, b) => Number(wells[b].y) - Number(wells[a].y))));
  }
  spec.wellGroups = wellGroups;
  spec.sourceDefinition = structuredClone(defn);
  return { spec, warnings: [] };
}
