import { describe, expect, it } from "vitest";
import type { EquipmentSnapshot } from "@/types/api";
import {
  formatMassG,
  isPlateWeigher,
  parsePlateWeigher,
  stabilityLabel,
} from "./plate-weigher";

function snapshot(over: Record<string, unknown> = {}): EquipmentSnapshot {
  const { details = {}, metrics = {}, components } = over as Record<string, never>;
  return {
    id: "flex_plate_weigher",
    name: "Flex Plate Weigher",
    kind: "other",
    fetched_at: new Date().toISOString(),
    latency_ms: 5,
    fetch_error: null,
    status: {
      equipment_status: "ready",
      activity: "idle",
      allowed_actions: ["read", "tare", "raise", "lower", "open_lid", "close_lid", "park", "shutdown"],
      required_actions: [],
      metrics,
      components: components ?? {
        balance: { connected: true, state: "ready", message: null },
        lift: { connected: true, state: "ready", message: "Plate at weighing" },
        lid: { connected: true, state: "open", message: null },
      },
      details: {
        balance: { driver: "sartorius_sbi", units: "g", last_mass_g: 1.2345, last_stable: true },
        lift: { driver: "pca9685_servo", poses_deg: { raised: -80, weighing: 70, install: -90 } },
        lift_pose: "weighing",
        lid: { driver: "pca9685_servo", open: true },
        ...(details as object),
      },
    },
  } as unknown as EquipmentSnapshot;
}

describe("isPlateWeigher", () => {
  it("selects an `other` envelope with balance and lift drivers", () => {
    expect(isPlateWeigher(snapshot())).toBe(true);
  });

  it("does not claim the XPR balance or the chiller", () => {
    const xpr = snapshot({ details: { balance: undefined, lift: undefined } });
    expect(isPlateWeigher(xpr)).toBe(false);
    const chiller = { kind: "other", status: { components: { pump: {} }, details: { setpoint_limits_c: [-20, 30] } } };
    expect(isPlateWeigher(chiller)).toBe(false);
  });

  it("survives a failed poll that omits details", () => {
    expect(isPlateWeigher({ kind: "other", status: { components: null, details: null } })).toBe(false);
    expect(isPlateWeigher({ kind: "other" })).toBe(false);
  });
});

describe("parsePlateWeigher", () => {
  it("prefers metrics.mass_g over the last reading in details", () => {
    const state = parsePlateWeigher(snapshot({ metrics: { mass_g: { value: 2.5, unit: "g" } } }));
    expect(state.massG).toBe(2.5);
    expect(state.stable).toBe(true);
    expect(state.liftPose).toBe("weighing");
    expect(state.poses).toEqual(["raised", "weighing", "install"]);
    expect(state.lidFitted).toBe(true);
    expect(state.lidState).toBe("open");
  });

  it("reads underload as a condition with no mass", () => {
    const state = parsePlateWeigher(
      snapshot({ details: { balance: { driver: "sartorius_sbi", condition: "underload", last_mass_g: null } } }),
    );
    expect(state.condition).toBe("underload");
    expect(state.massG).toBeNull();
    expect(formatMassG(state)).toBe("     Low");
    expect(stabilityLabel(state)).toBe("Low — nothing on the pan");
  });

  it("formats the mass box like the OT-2 platebalance panel", () => {
    expect(formatMassG({ massG: 1.2345, condition: null })).toBe("  1.2345");
    expect(formatMassG({ massG: null, condition: null })).toBe("       —");
    expect(formatMassG({ massG: null, condition: "overload" })).toBe("    High");
  });

  it("knows when no lid is fitted", () => {
    const state = parsePlateWeigher(
      snapshot({
        components: {
          balance: { connected: true, state: "ready" },
          lift: { connected: true, state: "ready" },
        },
        details: { lid: undefined },
      }),
    );
    expect(state.lidFitted).toBe(false);
    expect(state.lidState).toBeNull();
  });
});
