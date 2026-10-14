import { describe, expect, it } from "vitest";

import {
  absorbanceCsv,
  exportFilename,
  fluorescenceCsv,
  isReaderRead,
  planHasReaderReads,
  runRecord,
  type ReaderRun,
} from "./plan-readings";

const wells = ["A1", "A2", "A3"];
const steps = [
  { action: "plate.load", args: { plate_id: "plate-1", model: "square_96_19mm" } },
  { action: "drawer.close", args: {} },
  { action: "read.absorbance", args: { wells, wavelength_nm: 300 } },
  { action: "read.absorbance", args: { wells, wavelength_nm: 305 } },
  { action: "read.fluorescence", args: { wells, excitation_nm: 365, emission_nm: 400, focal_height_mm: 7 } },
];

function run(readings: ReaderRun["readings"], status: ReaderRun["status"] = "executed"): ReaderRun {
  return {
    context: {
      plan_id: "plan/abc 1",
      step_hash: "h",
      equipment_id: "cytation_5",
      equipment_name: "BioTek Cytation 5",
      actor: "someone@example.org",
      steps,
    },
    started_at: "2026-10-14T16:00:00.000Z",
    finished_at: status === "running" ? null : "2026-10-14T16:10:00.000Z",
    status,
    halt_reason: status === "failed" ? "step 4 (read.absorbance) failed: 503: busy" : null,
    readings,
  };
}

const abs300 = {
  index: 3,
  action: "read.absorbance" as const,
  args: steps[2].args,
  received_at: "2026-10-14T16:01:00.000Z",
  response: { wells: { A1: 0.123456789, A2: null, A3: null }, over_range: ["A2"] },
};
const abs305 = {
  index: 4,
  action: "read.absorbance" as const,
  args: steps[3].args,
  received_at: "2026-10-14T16:02:00.000Z",
  response: { wells: { A1: 0.1, A2: null }, over_range: ["A2"] },
};
const fl400 = {
  index: 5,
  action: "read.fluorescence" as const,
  args: steps[4].args,
  received_at: "2026-10-14T16:03:00.000Z",
  response: { wells: { A1: 1200, A2: 0, A3: 15 }, over_range: [] },
};

describe("plan-readings", () => {
  it("recognizes only plate-reader absorbance and fluorescence reads", () => {
    expect(isReaderRead("plate_reader", "read.absorbance")).toBe(true);
    expect(isReaderRead("plate_reader", "read.luminescence")).toBe(false);
    expect(isReaderRead("liquid_handler", "read.absorbance")).toBe(false);
    expect(planHasReaderReads("plate_reader", steps)).toBe(true);
    expect(planHasReaderReads("plate_reader", steps.slice(0, 2))).toBe(false);
  });

  it("writes raw values, over-range as empty + true, never zero", () => {
    expect(absorbanceCsv(run([abs300]))).toBe(
      "wavelength_nm,well,absorbance,over_range\n" +
        "300,A1,0.123456789,false\n" +
        "300,A2,,true\n" +
        "300,A3,,false\n"
    );
  });

  it("keeps a real zero and labels fluorescence as RFU", () => {
    expect(fluorescenceCsv(run([fl400]))).toBe(
      "excitation_nm,emission_nm,well,fluorescence_rfu,over_range\n" +
        "365,400,A1,1200,false\n" +
        "365,400,A2,0,false\n" +
        "365,400,A3,15,false\n"
    );
  });

  it("reports unlisted nulls and missing wells separately from over-range", () => {
    const record = runRecord(run([abs300, abs305]));
    expect(record.absorbance.over_range_wells).toEqual({ A2: ["300 nm", "305 nm"] });
    expect(record.absorbance.unexpected_nulls).toEqual([
      { step: 3, at: "300 nm", well: "A3", kind: "null" },
      { step: 4, at: "305 nm", well: "A3", kind: "missing" },
    ]);
    // Saved = numeric or over-range; the unexpected ones are not counted.
    expect(record.absorbance.saved_well_readings).toBe(4);
  });

  it("summarizes a partial run with the last saved wavelength", () => {
    const record = runRecord(run([abs300], "failed"));
    expect(record.status).toBe("failed");
    expect(record.halt_reason).toContain("step 4");
    expect(record.plate).toEqual({ plate_id: "plate-1", model: "square_96_19mm" });
    expect(record.absorbance).toMatchObject({ planned_reads: 2, completed_reads: 1, last_saved: "300 nm" });
    expect(record.fluorescence).toMatchObject({ planned_reads: 1, completed_reads: 0, last_saved: null, units: "RFU (raw)" });
    expect(record.absorbance.requested_wells).toEqual(wells);
    expect(record.readings).toHaveLength(1);
  });

  it("makes filesystem-safe filenames", () => {
    expect(exportFilename(run([]), "absorbance")).toBe("plan_abc_1-absorbance.csv");
    expect(exportFilename(run([]), "record")).toBe("plan_abc_1-record.json");
  });
});
