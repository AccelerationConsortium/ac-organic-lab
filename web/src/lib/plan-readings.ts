/**
 * Plate-reader results from an assistant plan, kept in this browser tab.
 *
 * The plan runner (UI_DESIGN §5 Step 1i) sends each step through the control
 * passthrough, which returns the device's response but records only the
 * outcome in the audit trail. A read's response IS the measurement, so for
 * `read.absorbance` / `read.fluorescence` steps the runner hands it here, and
 * the operator downloads it. Nothing here is a BitacoraDB record: the export
 * says so, and recording the results there remains a separate step.
 *
 * Values are written exactly as returned — no blank subtraction, no
 * normalization. A `null` named in `over_range` is over-range (empty value,
 * `over_range=true`), never zero. A `null` or missing well NOT named there is
 * reported separately as unexpected rather than folded into over-range.
 */

export type ReaderReadAction = "read.absorbance" | "read.fluorescence";

export interface ReaderReading {
  /** 1-based plan step index, matching the audit row's `plan_id#index`. */
  index: number;
  action: ReaderReadAction;
  args: Record<string, unknown>;
  received_at: string;
  response: unknown;
}

export interface ReaderPlanContext {
  plan_id: string;
  step_hash: string;
  equipment_id: string;
  equipment_name: string;
  actor: string;
  steps: { action: string; args: Record<string, unknown> }[];
}

export interface ReaderRun {
  context: ReaderPlanContext;
  started_at: string;
  finished_at: string | null;
  status: "running" | "executed" | "failed";
  halt_reason: string | null;
  readings: ReaderReading[];
}

export function isReaderRead(kind: string, action: string): action is ReaderReadAction {
  return kind === "plate_reader" && (action === "read.absorbance" || action === "read.fluorescence");
}

export function planHasReaderReads(kind: string, steps: readonly { action: string }[]): boolean {
  return steps.some((s) => isReaderRead(kind, s.action));
}

interface WellPoint {
  well: string;
  value: number | null;
  over_range: boolean;
  /** null or absent without being named over-range. */
  unexpected: "null" | "missing" | null;
}

function points(reading: ReaderReading): WellPoint[] {
  const requested = Array.isArray(reading.args.wells) ? (reading.args.wells as unknown[]).map(String) : [];
  const body = (reading.response ?? {}) as { wells?: Record<string, unknown>; over_range?: unknown };
  const wells = body.wells && typeof body.wells === "object" ? body.wells : {};
  const over = new Set(Array.isArray(body.over_range) ? body.over_range.map(String) : []);
  // Requested order first, then anything the device returned unasked.
  const order = [...requested, ...Object.keys(wells).filter((w) => !requested.includes(w))];
  return order.map((well) => {
    const present = Object.prototype.hasOwnProperty.call(wells, well);
    const raw = present ? wells[well] : undefined;
    const value = typeof raw === "number" && Number.isFinite(raw) ? raw : null;
    const overRange = value === null && over.has(well);
    const unexpected = value !== null || overRange ? null : present ? "null" : "missing";
    return { well, value, over_range: overRange, unexpected };
  });
}

function csv(rows: (string | number | boolean)[][]): string {
  return rows.map((r) => r.map((c) => {
    const s = String(c);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }).join(",")).join("\n") + "\n";
}

export function absorbanceCsv(run: ReaderRun): string {
  const rows: (string | number | boolean)[][] = [["wavelength_nm", "well", "absorbance", "over_range"]];
  for (const r of run.readings.filter((x) => x.action === "read.absorbance")) {
    for (const p of points(r)) rows.push([String(r.args.wavelength_nm), p.well, p.value ?? "", p.over_range]);
  }
  return csv(rows);
}

/** Units: raw RFU, per the device's agent guide — comparable within a run. */
export function fluorescenceCsv(run: ReaderRun): string {
  const rows: (string | number | boolean)[][] = [
    ["excitation_nm", "emission_nm", "well", "fluorescence_rfu", "over_range"],
  ];
  for (const r of run.readings.filter((x) => x.action === "read.fluorescence")) {
    for (const p of points(r)) {
      rows.push([String(r.args.excitation_nm), String(r.args.emission_nm), p.well, p.value ?? "", p.over_range]);
    }
  }
  return csv(rows);
}

function scanKey(action: ReaderReadAction, args: Record<string, unknown>): string {
  return action === "read.absorbance"
    ? `${args.wavelength_nm} nm`
    : `ex ${args.excitation_nm} / em ${args.emission_nm} nm`;
}

function scanSummary(run: ReaderRun, action: ReaderReadAction) {
  const planned = run.context.steps.filter((s) => s.action === action);
  const got = run.readings.filter((r) => r.action === action);
  const overRange: Record<string, string[]> = {};
  const unexpected: { step: number; at: string; well: string; kind: "null" | "missing" }[] = [];
  let saved = 0;
  for (const r of got) {
    for (const p of points(r)) {
      if (p.value !== null || p.over_range) saved += 1;
      if (p.over_range) (overRange[p.well] ??= []).push(scanKey(action, r.args));
      if (p.unexpected) unexpected.push({ step: r.index, at: scanKey(action, r.args), well: p.well, kind: p.unexpected });
    }
  }
  const last = got[got.length - 1];
  return {
    planned_reads: planned.length,
    completed_reads: got.length,
    saved_well_readings: saved,
    requested_wells: planned[0] && Array.isArray(planned[0].args.wells) ? planned[0].args.wells : [],
    settings: planned.map((s) => ({ ...s.args, wells: undefined })),
    last_saved: last ? scanKey(action, last.args) : null,
    over_range_wells: overRange,
    unexpected_nulls: unexpected,
  };
}

/** Everything needed to interpret the CSVs, plus the raw responses. */
export function runRecord(run: ReaderRun) {
  const load = run.context.steps.find((s) => s.action === "plate.load");
  return {
    schema_version: 1,
    record_type: "assistant_plan_reader_results",
    notice:
      "Browser-side export of plate-reader responses from a dashboard assistant plan. " +
      "Not a BitacoraDB record; record these results there separately. Values are raw: " +
      "no blank subtraction or normalization. Fluorescence is raw RFU. Over-range wells " +
      "have an empty value and over_range=true; unexpected_nulls lists any null the device " +
      "did not name as over-range.",
    plan_id: run.context.plan_id,
    step_hash: run.context.step_hash,
    equipment_id: run.context.equipment_id,
    equipment_name: run.context.equipment_name,
    actor: run.context.actor,
    plate: load ? { plate_id: load.args.plate_id ?? null, model: load.args.model ?? null } : null,
    started_at: run.started_at,
    finished_at: run.finished_at,
    status: run.status,
    halt_reason: run.halt_reason,
    absorbance: scanSummary(run, "read.absorbance"),
    fluorescence: { units: "RFU (raw)", ...scanSummary(run, "read.fluorescence") },
    readings: run.readings,
  };
}

function safeName(s: string): string {
  return s.replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 80) || "plan";
}

export function exportFilename(run: ReaderRun, part: "absorbance" | "fluorescence" | "record"): string {
  const ext = part === "record" ? "json" : "csv";
  return `${safeName(run.context.plan_id)}-${part}.${ext}`;
}

export function downloadText(filename: string, content: string, type: string): void {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  try {
    document.body.appendChild(link);
    link.click();
  } finally {
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
