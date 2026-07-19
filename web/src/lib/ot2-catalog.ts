/**
 * Central, authored OT-2 labware / module catalog for the dashboard's deck
 * pickers.
 *
 * The gateway is deliberately unchanged (it owns runtime deck/plate/tip state
 * and accepts arbitrary declaration strings), so the *choices* offered to an
 * operator are authored here, centrally — separate from runtime state. An
 * entry's `declare` value is exactly what is sent to
 * `POST /control/deck/declare` through the central passthrough:
 *
 *   - labware — the exact Opentrons `load_name` (the gateway parses any
 *     string containing "_" as a load_name and derives kind/grid from it);
 *   - modules — one of the gateway's four declaration keys
 *     (`deck.py _MODULE_KINDS`): temperature_module, magnetic_module,
 *     heater_shaker_module, thermocycler_module;
 *   - legacy generics — bare kind strings ("96-well", "waste", …) kept so
 *     pre-existing declarations keep round-tripping and stay pickable.
 *
 * Declaring is *intent only*: it never loads labware on the robot and never
 * runs `/control/setup`. Custom (MatterLab) labware definitions and actual
 * setup execution are out of scope for this catalog.
 */

export type CatalogCategory =
  | "plate"
  | "reservoir"
  | "tiprack"
  | "tuberack"
  | "module"
  | "fixture" // trash / waste
  | "generic" // legacy bare-kind strings
  | "custom"; // lab-store definitions (GET /api/labware), passed in at runtime

export interface CatalogEntry {
  /** Stable key, unique across the catalog (used as React key / test id). */
  key: string;
  /** Human-readable picker label. */
  label: string;
  category: CatalogCategory;
  /** Exact string sent to /control/deck/declare (load_name, module key, or legacy kind). */
  declare: string;
  /** Well grid, when the geometry is known (drives the MiniPlate preview). */
  rows?: number;
  columns?: number;
  /** True for tip racks (styled distinctly on the deck). */
  isTiprack?: boolean;
  /** Optional compatibility notes surfaced in the picker/tooltip. */
  compat?: string;
}

export const OT2_CATALOG: CatalogEntry[] = [
  // ---- Standard Opentrons load_names in use by OT2Demo -------------------
  {
    key: "corning_96_wellplate_360ul_flat",
    label: "Corning 96-well plate, 360 µL flat",
    category: "plate",
    declare: "corning_96_wellplate_360ul_flat",
    rows: 8,
    columns: 12,
  },
  {
    key: "agilent_1_reservoir_290ml",
    label: "Agilent 1-well reservoir, 290 mL",
    category: "reservoir",
    declare: "agilent_1_reservoir_290ml",
    rows: 1,
    columns: 1,
  },
  {
    key: "opentrons_96_tiprack_300ul",
    label: "Opentrons 96 tip rack, 300 µL",
    category: "tiprack",
    declare: "opentrons_96_tiprack_300ul",
    rows: 8,
    columns: 12,
    isTiprack: true,
    compat: "P300 single/multi",
  },

  // ---- Gateway-supported module declaration keys --------------------------
  {
    key: "temperature_module",
    label: "Temperature module (GEN2)",
    category: "module",
    declare: "temperature_module",
    compat: "Sticky fixture; overhangs the slot to its left",
  },
  {
    key: "magnetic_module",
    label: "Magnetic module (GEN2)",
    category: "module",
    declare: "magnetic_module",
  },
  {
    key: "heater_shaker_module",
    label: "Heater-shaker module (GEN1)",
    category: "module",
    declare: "heater_shaker_module",
  },
  {
    key: "thermocycler_module",
    label: "Thermocycler module (GEN2)",
    category: "module",
    declare: "thermocycler_module",
    compat: "Occupies slots 7+10 physically; declare at 7",
  },

  // ---- Legacy generic kinds (round-trip + coarse intent) -------------------
  { key: "generic-96-well", label: "96-well plate (generic)", category: "generic", declare: "96-well", rows: 8, columns: 12 },
  { key: "generic-384-well", label: "384-well plate (generic)", category: "generic", declare: "384-well", rows: 16, columns: 24 },
  { key: "generic-24-well", label: "24-well plate (generic)", category: "generic", declare: "24-well", rows: 4, columns: 6 },
  { key: "generic-tiprack", label: "Tip rack (generic)", category: "generic", declare: "tiprack", rows: 8, columns: 12, isTiprack: true },
  { key: "generic-reservoir", label: "Reservoir (generic)", category: "generic", declare: "reservoir", rows: 1, columns: 12 },
  { key: "generic-tuberack", label: "Tube rack (generic)", category: "generic", declare: "tuberack", rows: 4, columns: 6 },
  { key: "waste", label: "Waste bin", category: "fixture", declare: "waste" },
];

export const CATEGORY_LABELS: Record<CatalogCategory, string> = {
  plate: "Plates",
  reservoir: "Reservoirs",
  tiprack: "Tip racks",
  tuberack: "Tube racks",
  module: "Modules",
  fixture: "Fixtures",
  generic: "Generic (legacy)",
  custom: "Custom (lab store)",
};

/** Display order for grouped pickers. */
export const CATEGORY_ORDER: CatalogCategory[] = [
  "plate",
  "reservoir",
  "tiprack",
  "tuberack",
  "custom",
  "module",
  "fixture",
  "generic",
];

/** Look an entry up by the exact string a declaration carries (load_name,
 *  module key, or legacy kind). Returns null for unknown/custom strings —
 *  those still round-trip verbatim, they just aren't catalog picks. */
export function catalogEntryForDeclare(
  declare: string | undefined | null,
  extraEntries: CatalogEntry[] = [],
): CatalogEntry | null {
  if (!declare) return null;
  return (
    OT2_CATALOG.find((e) => e.declare === declare) ??
    extraEntries.find((e) => e.declare === declare) ??
    null
  );
}

/** Group the catalog (optionally filtered by a search query) for a picker.
 *  Matches on label, declare string, key, and category label; empty query
 *  returns everything. Groups follow CATEGORY_ORDER; empty groups dropped.
 *  `extraEntries` lets callers merge runtime entries (the lab store's custom
 *  definitions from GET /api/labware) into the authored catalog. */
export function groupedCatalog(
  query = "",
  extraEntries: CatalogEntry[] = [],
): { category: CatalogCategory; label: string; entries: CatalogEntry[] }[] {
  const q = query.trim().toLowerCase();
  const matches = (e: CatalogEntry) =>
    !q ||
    e.label.toLowerCase().includes(q) ||
    e.declare.toLowerCase().includes(q) ||
    e.key.toLowerCase().includes(q) ||
    CATEGORY_LABELS[e.category].toLowerCase().includes(q);
  const all = [...OT2_CATALOG, ...extraEntries];
  return CATEGORY_ORDER.map((category) => ({
    category,
    label: CATEGORY_LABELS[category],
    entries: all.filter((e) => e.category === category && matches(e)),
  })).filter((g) => g.entries.length > 0);
}

/** Map a lab-store summary (GET /api/labware) to a picker entry. */
export function catalogEntryFromLabware(summary: {
  load_name: string;
  display_name: string;
  is_tiprack?: boolean;
  rows?: number;
  columns?: number;
  source?: string;
}): CatalogEntry {
  return {
    key: `labware-store-${summary.load_name}`,
    label: summary.display_name || summary.load_name,
    category: "custom",
    declare: summary.load_name,
    rows: summary.rows || undefined,
    columns: summary.columns || undefined,
    isTiprack: summary.is_tiprack || undefined,
    compat: summary.source === "repo" ? "Repo-committed definition" : "Uploaded definition",
  };
}
