// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { PlateReaderTile } from "./PlateReaderTile";

vi.mock("@/lib/use-control-lock", () => ({
  useControlLock: () => ({
    locked: false,
    noAccess: false,
    countdown: 0,
    unlock: vi.fn(),
    lock: vi.fn(),
    toggle: vi.fn(),
  }),
}));

const postPlateReaderSetTemperature = vi.fn(async () => ({ ok: true }));
const postPlateReaderStopTemperature = vi.fn(async () => ({ ok: true }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  postPlateReaderSetTemperature: (id: string, celsius: number) =>
    postPlateReaderSetTemperature(id, celsius),
  postPlateReaderStopTemperature: (id: string) =>
    postPlateReaderStopTemperature(id),
}));

function snapshot(over: {
  equipment_status?: string;
  allowed_actions?: string[];
  incubator_state?: string;
  actual_c?: number | null;
  setpoint_c?: number | null;
  claimed_by?: { session_id: string; owner: string; expires_at: string } | null;
}): EquipmentSnapshot {
  const metrics: Record<string, { value: number; unit: string }> = {};
  if (over.actual_c != null) {
    metrics.actual_temperature = { value: over.actual_c, unit: "C" };
  }
  if (over.setpoint_c != null) {
    metrics.setpoint_temperature = { value: over.setpoint_c, unit: "C" };
  }
  return {
    id: "cytation_5",
    name: "BioTek Cytation 5",
    kind: "plate_reader",
    fetched_at: "2026-08-21T00:00:00Z",
    latency_ms: 10,
    fetch_error: null,
    status: {
      protocol_version: "1.2",
      equipment_id: "cytation_5",
      equipment_name: "BioTek Cytation 5",
      equipment_kind: "plate_reader",
      equipment_status: over.equipment_status ?? "ready",
      device_time: "2026-08-21T00:00:00Z",
      required_actions: [],
      allowed_actions: over.allowed_actions ?? [
        "startup",
        "shutdown",
        "incubator.set_temperature",
        "incubator.stop",
      ],
      details: {
        temperature_range_c: { min: 4, max: 45 },
        claimed_by: over.claimed_by ?? null,
      },
      metrics,
      components: {
        incubator: {
          connected: true,
          state: over.incubator_state ?? "off",
          message: null,
        },
        optics: { connected: true, state: "idle", message: null },
        plate_stage: { connected: true, state: "in", message: null },
        imaging: { connected: true, state: "idle", message: null },
      },
    },
  } as unknown as EquipmentSnapshot;
}

afterEach(() => {
  cleanup();
  postPlateReaderSetTemperature.mockClear();
  postPlateReaderStopTemperature.mockClear();
});

describe("PlateReaderTile incubator controls", () => {
  it("posts incubator.set_temperature with the typed celsius value", () => {
    render(
      <PlateReaderTile
        snapshot={snapshot({ actual_c: 22, incubator_state: "off" })}
      />,
    );
    const input = screen.getByLabelText("Incubator setpoint in degrees C");
    fireEvent.change(input, { target: { value: "37" } });
    fireEvent.click(screen.getByRole("button", { name: "Set" }));
    expect(postPlateReaderSetTemperature).toHaveBeenCalledWith("cytation_5", 37);
  });

  it("lets Off stop a running incubator", () => {
    render(
      <PlateReaderTile
        snapshot={snapshot({
          actual_c: 36.8,
          setpoint_c: 37,
          incubator_state: "at_setpoint",
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Off" }));
    expect(postPlateReaderStopTemperature).toHaveBeenCalledWith("cytation_5");
  });

  it("disables Off when the incubator is already off", () => {
    render(
      <PlateReaderTile
        snapshot={snapshot({ actual_c: 22, incubator_state: "off" })}
      />,
    );
    expect(
      (screen.getByRole("button", { name: "Off" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(postPlateReaderStopTemperature).not.toHaveBeenCalled();
  });

  it("withholds Set when the device does not advertise the action", () => {
    render(
      <PlateReaderTile
        snapshot={snapshot({
          allowed_actions: ["shutdown"],
          actual_c: 22,
        })}
      />,
    );
    expect(
      (screen.getByRole("button", { name: "Set" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});

describe("PlateReaderTile while a workflow holds the claim", () => {
  const CLAIM = {
    session_id: "f1f1c1a2",
    owner: "agent:solubility-screening",
    expires_at: "2026-08-21T00:00:30Z",
  };

  it("names the holder so the reader does not look idle and free", () => {
    render(<PlateReaderTile snapshot={snapshot({ claimed_by: CLAIM })} />);
    expect(
      screen.getByText("In use by agent:solubility-screening"),
    ).toBeTruthy();
  });

  it("disables the controls a claim would make 423", () => {
    // The dashboard passthrough takes its own per-request claim, so it can
    // never acquire one the device is already holding for someone else.
    // Every control here would fail; disabling beats an error toast.
    render(
      <PlateReaderTile
        snapshot={snapshot({
          claimed_by: CLAIM,
          actual_c: 36.8,
          setpoint_c: 37,
          incubator_state: "at_setpoint",
        })}
      />,
    );
    expect(
      (screen.getByRole("button", { name: "Set" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "Off" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Off" }));
    expect(postPlateReaderStopTemperature).not.toHaveBeenCalled();
  });

  it("says nothing when no claim is held", () => {
    render(<PlateReaderTile snapshot={snapshot({ actual_c: 22 })} />);
    expect(screen.queryByText(/In use by/)).toBeNull();
  });

  it("leaves the status pill alone — a claim is not an operation", () => {
    // STATUS_SPEC §2.3: a claimed but idle reader is `ready`. The banner
    // exists precisely because the state enum cannot say "reserved".
    render(<PlateReaderTile snapshot={snapshot({ claimed_by: CLAIM })} />);
    expect(screen.getByText("In use by agent:solubility-screening")).toBeTruthy();
    expect(screen.queryByText("busy")).toBeNull();
  });
});
