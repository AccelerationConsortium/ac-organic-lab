/**
 * Reader for an Opentrons schema-2 labware definition → the geometry the
 * inspector's two views need.
 *
 * The dashboard serves whole definitions from its labware API; this turns one into millimetre geometry:
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
  /** Measured internal profile, in millimetres above the well bottom. */
  sections?: { bottomHeight: number; topHeight: number; bottomDiameter: number; topDiameter: number }[];
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
  // Ordering describes access order, not a rectangular physical grid. Mixed
  // tube racks legitimately have columns of different lengths and spacing.
  if (ordering.some((col) => col.length === 0)) return null;
  const orderedNames = ordering.flat();
  if (new Set(orderedNames).size !== orderedNames.length) return null;

  const wellsRaw = (d.wells ?? {}) as Record<string, unknown>;
  const wells: Record<string, WellGeometry> = {};
  for (const [well, raw] of Object.entries(wellsRaw)) {
    const parsed = readWell(raw);
    if (parsed) {
      const id = (raw as Record<string, unknown>).geometryDefinitionId;
      const inner = d.innerLabwareGeometry as Record<string, { sections?: Record<string, unknown>[] }> | undefined;
      const sections = typeof id === "string" ? inner?.[id]?.sections : undefined;
      if (Array.isArray(sections)) {
        const profile = sections.flatMap((s) => {
          const bottomHeight = num(s.bottomHeight), topHeight = num(s.topHeight);
          if (bottomHeight == null || topHeight == null || topHeight < bottomHeight) return [];
          if (s.shape === "conical") {
            const bottomDiameter = num(s.bottomDiameter), topDiameter = num(s.topDiameter);
            return bottomDiameter != null && topDiameter != null
              ? [{ bottomHeight, topHeight, bottomDiameter, topDiameter }] : [];
          }
          if (s.shape === "spherical") {
            const radius = num(s.radiusOfCurvature);
            if (radius == null || radius <= 0 || topHeight > 2 * radius) return [];
            const diameter = (h: number) => 2 * Math.sqrt(Math.max(0, 2 * radius * h - h * h));
            return Array.from({ length: 8 }, (_, i) => {
              const bottom = bottomHeight + (topHeight - bottomHeight) * i / 8;
              const top = bottomHeight + (topHeight - bottomHeight) * (i + 1) / 8;
              return { bottomHeight: bottom, topHeight: top, bottomDiameter: diameter(bottom), topDiameter: diameter(top) };
            });
          }
          return [];
        });
        // An unsupported section must not become an invented complete profile.
        if (sections.every((s) => s.shape === "conical" || s.shape === "spherical") && profile.length) {
          parsed.sections = profile.sort((a, b) => b.topHeight - a.topHeight);
        }
      }
      wells[well] = parsed;
    }
  }
  if (Object.keys(wells).length === 0) return null;
  if (ordering.some((col) => col.some((well) => wells[well] == null))) return null;
  if (Object.keys(wells).length !== orderedNames.length) return null;

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
    rows: Math.max(...ordering.map((col) => col.length)),
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

/** Wells that overlap in a front projection share x, regardless of ordering. */
export function frontProjectionColumns(geometry: LabwareGeometry): string[][] {
  const byX = new Map<number, string[]>();
  for (const name of geometry.ordering.flat()) {
    const x = geometry.wells[name].x;
    byX.set(x, [...(byX.get(x) ?? []), name]);
  }
  return [...byX.entries()].sort(([a], [b]) => a - b).map(([, names]) =>
    names.sort((a, b) => geometry.wells[a].y - geometry.wells[b].y));
}

/** One run of the body's top edge in the elevation: `x0..x1` mm wide, `top` mm tall. */
export interface ElevationStep {
  x0: number;
  x1: number;
  top: number;
}

/** Faces must tile the footprint to this tolerance (mm) to be believed. */
const TILING_TOLERANCE_MM = 1;

/**
 * The body's top edge across x, left to right, in mm.
 *
 * Nearly all labware is a box of one height, so this is a single step at
 * `footprintZ`. A **stepped block** is not: the tip-length calibration block's
 * two "wells" are the flat top faces of its short and tall halves, at 33 mm and
 * 62.5 mm, and `zDimension` records only the taller one. Drawn as a full-height
 * box it loses the step the block exists for — and with it the only thing that
 * distinguishes `short_side_left` from `short_side_right`.
 *
 * A zero-depth well is a face, not a cavity, so when *every* column is one and
 * the faces tile the footprint in x with the tallest reaching `footprintZ`, the
 * faces are the top edge. Anything less than that and we would be inventing a
 * silhouette, so fall back to the box.
 */
export function elevationProfile(geometry: LabwareGeometry): ElevationStep[] {
  const box: ElevationStep[] = [{ x0: 0, x1: geometry.footprintX, top: geometry.footprintZ }];
  const faces: ElevationStep[] = [];
  for (const column of geometry.ordering) {
    const wells = column.map((well) => geometry.wells[well]).filter((w) => w != null);
    if (wells.length === 0 || wells.length !== column.length) return box;
    const [first] = wells;
    if (wells.some((w) => w.depth !== 0 || w.z !== first.z)) return box;
    const halfX = wellHalfX(first);
    if (halfX <= 0 || wells.some((w) => w.x !== first.x)) return box;
    faces.push({ x0: first.x - halfX, x1: first.x + halfX, top: first.z });
  }
  faces.sort((a, b) => a.x0 - b.x0);

  const spans =
    Math.abs(faces[0].x0) <= TILING_TOLERANCE_MM &&
    Math.abs(faces[faces.length - 1].x1 - geometry.footprintX) <= TILING_TOLERANCE_MM &&
    faces.every((face, i) => i === 0 || Math.abs(face.x0 - faces[i - 1].x1) <= TILING_TOLERANCE_MM);
  const reachesTop =
    Math.abs(Math.max(...faces.map((f) => f.top)) - geometry.footprintZ) <= TILING_TOLERANCE_MM;
  if (!spans || !reachesTop) return box;

  // Snap to the footprint and split each seam down the middle, so the
  // definition's rounding (2 x 63.88 for a 127.75 mm block) leaves no sliver.
  return faces.map((face, i) => ({
    x0: i === 0 ? 0 : (faces[i - 1].x1 + face.x0) / 2,
    x1: i === faces.length - 1 ? geometry.footprintX : (face.x1 + faces[i + 1].x0) / 2,
    top: face.top,
  }));
}

/** Half-depth (mm) of a well in the y axis, for the top-down plan. */
export function wellHalfY(well: WellGeometry): number {
  if (well.shape === "rectangular") return (well.yDimension ?? 0) / 2;
  return (well.diameter ?? 0) / 2;
}
