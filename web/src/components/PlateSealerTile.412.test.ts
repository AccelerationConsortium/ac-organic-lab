// Unit tests for plateloc's three seal.start 412 body shapes (device v1.4+)
// and the shared last_error.code → recovery copy table.
import { describe, expect, it } from "vitest";

import { parseSealer412, recoveryForCode } from "./PlateSealerTile";

const ctx = { action: "seal.start", retryAfterS: null };

describe("parseSealer412", () => {
  it("renders the stage interlock shape", () => {
    const msg = parseSealer412(
      { detail: "Stage not loaded", stage_state: "out", required: "in" },
      ctx,
    );
    expect(msg).toContain("stage is out");
    expect(msg).toContain('"Stage in"');
  });

  it("renders the temperature interlock shape with retry", () => {
    const msg = parseSealer412(
      {
        detail: "Temperature outside seal band",
        actual_c: 166.0,
        setpoint_c: 170.0,
        tolerance_c: 2.0,
        retry_after_s: 2,
      },
      ctx,
    );
    expect(msg).toBe("Heater at 166 °C, need 170 ±2 °C. Try again in ~2 s.");
  });

  it("renders the v1.4 health interlock shape via the recovery table", () => {
    const msg = parseSealer412(
      {
        detail: "Uncleared fault",
        last_error_code: "low_air_pressure",
        last_error_message: "Low Air Pressure Error",
        retry_after_s: 47,
      },
      ctx,
    );
    expect(msg).toBe(
      "Clear the last fault first — Air supply low. Check the regulator at ~80 psi." +
        " Retrying in ~47 s also clears it.",
    );
  });

  it("falls back to the device message for unmapped health codes", () => {
    const msg = parseSealer412(
      {
        detail: "Uncleared fault",
        last_error_code: "brand_new_code",
        last_error_message: "something novel broke",
      },
      ctx,
    );
    expect(msg).toContain("something novel broke");
  });

  it("only fires for seal.start and returns null for unknown shapes", () => {
    expect(
      parseSealer412({ stage_state: "out" }, { action: "stage.in", retryAfterS: null }),
    ).toBeNull();
    expect(parseSealer412({ detail: "???" }, ctx)).toBeNull();
  });
});

describe("recoveryForCode", () => {
  it("maps the v1.3.2 additions", () => {
    expect(recoveryForCode("no_plate")).toContain("Load a plate");
    expect(recoveryForCode("vacuum_error")).toContain("compressed-air");
  });

  it("returns null for unknown / absent codes (band falls back to raw message)", () => {
    expect(recoveryForCode("nope")).toBeNull();
    expect(recoveryForCode(null)).toBeNull();
  });
});
