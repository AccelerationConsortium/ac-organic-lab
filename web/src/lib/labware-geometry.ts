/**
 * Reader for an Opentrons schema-2 labware definition → the geometry the
 * inspector's two views need.
 *
 * The dashboard serves whole definitions at `GET /api/labware/standard/{load_name}`
 * (and `/api/labware/{load_name}` for repo / uploaded ones); this turns one into
 * millimetre geometry:
 * footprint, per-well position / depth / shape, and the tip length for a rack.
 * Kept separate from rendering so the same numbers drive the top-down plan and
 * the side elevation, and so a definition the gateway cannot supply degrades to
 * `null` in exactly one place.
 *
 * Coordinates are the definition's own: origin at the slot's front-left-bottom
 * corner, `x` right, `y` back, `z` **up from the slot floor to the well
 * bottom** — so a well's mouth sits at `z + depth`, which is what lets the
 * elevation draw a cavity that starts below the labware's top face (a tip rack's
 * tips stand proud of the plate; a deep-well plate's wells do not).
 */

export interface WellGeometry {
  /** Well centre, mm from the slot's front-left corner. */
  x: number;
  y: number;
  /** Well **bottom**, mm above the slot floor. Mouth is at `z + depth`. */
  z: number;
  depth: number;
  shape: "circular" | "rectangular";
  /** Circular wells only. */
  diameter?: number;
  /** Rectangular wells only. */
  xDimension?: number;
  yDimension?: number;
  totalLiquidVolume?: number | null;
}

export interface LabwareGeometry {
  loadName: string;
  displayName: string;
  footprintX: number;
  footprintY: number;
  footprintZ: number;
  rows: number;
  columns: number;
  isTiprack: boolean;
  /** Rack tip length in mm, else null. */
  tipLength: number | null;
  /** Column-major well ids, exactly as the definition orders them:
   *  `ordering[column][row]`, so `ordering[0]` is A1..H1. */
  ordering: string[][];
  wells: Record<string, WellGeometry>;
  wellVolumeUl: number | null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readWell(raw: unknown): WellGeometry | null {
  if (!raw || typeof raw !== "object") return null;
  const w = raw as Record<string, unknown>;
  const x = num(w.x);
  const y = num(w.y);
  const depth = num(w.depth);
  if (x == null || y == null || depth == null) return null;
  const shape = w.shape === "rectangular" ? "rectangular" : "circular";
  return {
    x,
    y,
    z: num(w.z) ?? 0,
    depth,
    shape,
    diameter: num(w.diameter) ?? undefined,
    xDimension: num(w.xDimension) ?? undefined,
    yDimension: num(w.yDimension) ?? undefined,
    totalLiquidVolume: num(w.totalLiquidVolume),
  };
}

/**
 * Parse a definition, or return `null` when it is not usable geometry — a
 * missing footprint or an empty well map means the elevation would have to
 * invent dimensions, and an invented cross-section is worse than none.
 */
export function geometryFromDefinition(defn: unknown): LabwareGeometry | null {
  if (!defn || typeof defn !== "object") return null;
  const d = defn as Record<string, unknown>;
  const dims = (d.dimensions ?? {}) as Record<string, unknown>;
  const footprintX = num(dims.xDimension);
  const footprintY = num(dims.yDimension);
  const footprintZ = num(dims.zDimension);
  if (footprintX == null || footprintY == null || footprintZ == null) return null;

  const orderingRaw = Array.isArray(d.ordering) ? d.ordering : [];
  const ordering = orderingRaw
    .filter((col): col is unknown[] => Array.isArray(col))
    .map((col) => col.filter((w): w is string => typeof w === "string"));
  if (ordering.length === 0 || ordering[0].length === 0) return null;

  const wellsRaw = (d.wells ?? {}) as Record<string, unknown>;
  const wells: Record<string, WellGeometry> = {};
  for (const [well, raw] of Object.entries(wellsRaw)) {
    const parsed = readWell(raw);
    if (parsed) wells[well] = parsed;
  }
  if (Object.keys(wells).length === 0) return null;

  const params = (d.parameters ?? {}) as Record<string, unknown>;
  const meta = (d.metadata ?? {}) as Record<string, unknown>;
  const loadName = typeof params.loadName === "string" ? params.loadName : "";
  const first = wells[ordering[0][0]] ?? Object.values(wells)[0];

  return {
    loadName,
    displayName: typeof meta.displayName === "string" ? meta.displayName : loadName,
    footprintX,
    footprintY,
    footprintZ,
    rows: ordering[0].length,
    columns: ordering.length,
    isTiprack: params.isTiprack === true,
    tipLength: num(params.tipLength),
    ordering,
    wells,
    wellVolumeUl: first?.totalLiquidVolume ?? null,
  };
}

/** Half-width (mm) of a well in the x axis — the elevation's cavity half-width. */
export function wellHalfX(well: WellGeometry): number {
  if (well.shape === "rectangular") return (well.xDimension ?? 0) / 2;
  return (well.diameter ?? 0) / 2;
}

/** Half-depth (mm) of a well in the y axis, for the top-down plan. */
export function wellHalfY(well: WellGeometry): number {
  if (well.shape === "rectangular") return (well.yDimension ?? 0) / 2;
  return (well.diameter ?? 0) / 2;
}
