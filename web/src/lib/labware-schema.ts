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

// OT-2 slot physical limits (mm) — keep in sync with api/app/labware.py.
export const MAX_DIMENSIONS = { x: 127, y: 85.5, z: 200 } as const;

export const LOAD_NAME_RE = /^[a-z0-9._]+$/;

export type WellShape = "circular" | "rectangular";
export type DisplayCategory = "wellPlate" | "reservoir" | "tipRack" | "tubeRack";

export interface LabwareSpec {
  loadName: string;
  displayName: string;
  brand: string;
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

/** A sensible starting spec (96-well SLAS plate geometry). */
export function defaultSpec(): LabwareSpec {
  return {
    loadName: "",
    displayName: "",
    brand: "",
    displayCategory: "wellPlate",
    rows: 8,
    columns: 12,
    footprintX: 127,
    footprintY: 85.5,
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
  const issues: ValidationIssue[] = [];
  const err = (field: ValidationIssue["field"], message: string) =>
    issues.push({ field, message });

  if (!LOAD_NAME_RE.test(spec.loadName)) {
    err("loadName", "Lowercase letters, digits, dot and underscore only.");
  } else if (!spec.loadName.includes("_")) {
    err("loadName", "Must contain at least one underscore (e.g. brand_24_vialplate_2ml).");
  }
  if (!spec.displayName.trim()) err("displayName", "Display name is required.");
  if (!spec.brand.trim()) err("brand", "Brand is required (e.g. MatterLab).");

  if (!Number.isInteger(spec.rows) || spec.rows < 1) err("rows", "At least 1 row.");
  if (!Number.isInteger(spec.columns) || spec.columns < 1) err("columns", "At least 1 column.");

  for (const [field, value, max] of [
    ["footprintX", spec.footprintX, MAX_DIMENSIONS.x],
    ["footprintY", spec.footprintY, MAX_DIMENSIONS.y],
    ["footprintZ", spec.footprintZ, MAX_DIMENSIONS.z],
  ] as const) {
    if (!(value > 0)) err(field, "Must be positive.");
    else if (value > max) err(field, `Exceeds the OT-2 slot limit (${max} mm).`);
  }

  if (spec.wellShape === "circular") {
    if (!(spec.wellDiameter && spec.wellDiameter > 0))
      err("wellDiameter", "Diameter is required for circular wells.");
  } else {
    if (!(spec.wellXDimension && spec.wellXDimension > 0))
      err("wellXDimension", "X size is required for rectangular wells.");
    if (!(spec.wellYDimension && spec.wellYDimension > 0))
      err("wellYDimension", "Y size is required for rectangular wells.");
  }
  if (!(spec.wellDepth > 0)) err("wellDepth", "Well depth must be positive.");
  else if (spec.wellDepth > spec.footprintZ)
    err("wellDepth", "Well depth cannot exceed the overall height.");
  if (!(spec.wellVolumeUl > 0)) err("wellVolumeUl", "Volume must be positive.");
  if (spec.displayCategory === "tipRack" && !(spec.tipLength && spec.tipLength > 0))
    err("tipLength", "Tip length is required for tip racks.");

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
    brand: { brand: spec.brand },
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

/**
 * Best-effort inverse of {@link buildDefinition}: populate the parametric
 * form from an existing schema-2 definition so it can be modified and
 * re-saved. Geometry is derived from A1 (offsets/shape/depth/volume), its
 * neighbours (spacing), and `ordering` (grid). Non-uniform ("irregular")
 * labware loses per-well detail — the returned `warnings` say so.
 */
export function specFromDefinition(defn: Record<string, unknown>): {
  spec: LabwareSpec;
  warnings: string[];
} {
  const warnings: string[] = [];
  const d = defn as {
    metadata?: { displayName?: string; displayCategory?: string };
    brand?: { brand?: string };
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
  const category = d.metadata?.displayCategory;
  if (
    category === "wellPlate" ||
    category === "reservoir" ||
    category === "tipRack" ||
    category === "tubeRack"
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

  // Flag non-uniform geometry the parametric form cannot represent.
  const wellList = Object.values(wells);
  const uniform = wellList.every(
    (w) =>
      w.depth === a1.depth &&
      w.shape === a1.shape &&
      (a1.shape === "rectangular"
        ? w.xDimension === a1.xDimension && w.yDimension === a1.yDimension
        : w.diameter === a1.diameter),
  );
  if (!uniform) {
    warnings.push(
      "Wells are not uniform — the form models one well geometry, so per-well differences will be lost on rebuild.",
    );
  }

  return { spec, warnings };
}
