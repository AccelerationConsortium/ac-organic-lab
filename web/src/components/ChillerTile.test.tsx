// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EquipmentSnapshot } from "@/types/api";
import { ChillerTile } from "./ChillerTile";

const mocks = vi.hoisted(() => ({
  locked: false,
  setTemperature: vi.fn(async () => ({ ok: true })),
  setPumpSpeed: vi.fn(async () => ({ ok: true })),
  start: vi.fn(async () => ({ ok: true })),
  stop: vi.fn(async () => ({ ok: true })),
  startup: vi.fn(async () => ({ ok: true })),
}));
vi.mock("@/lib/use-control-lock", () => ({
  useControlLock: () => ({
    locked: mocks.locked,
    countdown: 0,
    toggle: vi.fn(),
  }),
}));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<object>()),
  postChillerSetTemperature: mocks.setTemperature,
  postChillerSetPumpSpeed: mocks.setPumpSpeed,
  postChillerStart: mocks.start,
  postChillerStop: mocks.stop,
  postChillerStartup: mocks.startup,
}));

const ALL_ACTIONS = [
  "startup",
  "shutdown",
  "chiller.stop",
  "chiller.reset",
  "chiller.set_temperature",
  "chiller.set_pump_speed",
  "chiller.start",
];

function snapshot(over: Record<string, unknown> = {}): EquipmentSnapshot {
  const {
    equipment_status = "ready",
    activity = "idle",
    allowed_actions = ALL_ACTIONS,
    details = {},
    metrics = {
      actual_temperature: { value: 4.9, unit: "C" },
      setpoint_temperature: { value: 5, unit: "C" },
      pump_speed: { value: 0, unit: "rpm" },
    },
  } = over as Record<string, never>;
  return {
    id: "lle_chiller",
    name: "IKA RC 2 lite Chiller",
    kind: "other",
    activity,
    fetched_at: new Date().toISOString(),
    latency_ms: 10,
    fetch_error: null,
    status: {
      equipment_status,
      activity,
      allowed_actions,
      required_actions: [],
      metrics,
      components: {
        pump: { connected: true, state: activity },
        tempering: {
          connected: true,
          state: "at_setpoint",
          message: "compressor state is not readable over the NAMUR interface",
        },
      },
      details: {
        setpoint_limits_c: [-20, 30],
        hardware_state_observed: true,
        tempering_commanded: false,
        ...(details as object),
      },
    },
  } as unknown as EquipmentSnapshot;
}

const setpointInput = () => screen.getByLabelText("Chiller setpoint in degrees C");

afterEach(() => {
  cleanup();
  for (const fn of [
    mocks.setTemperature,
    mocks.setPumpSpeed,
    mocks.start,
    mocks.stop,
    mocks.startup,
  ]) {
    fn.mockReset();
    fn.mockResolvedValue({ ok: true });
  }
  mocks.locked = false;
});

describe("ChillerTile", () => {
  it("shows the bath reading and the setpoint from the envelope", () => {
    render(<ChillerTile snapshot={snapshot()} />);
    expect(screen.getByText("4.9 °C")).toBeTruthy();
    expect((setpointInput() as HTMLInputElement).value).toBe("5.0");
  });

  it("sends a new setpoint once", async () => {
    render(<ChillerTile snapshot={snapshot()} />);
    fireEvent.change(setpointInput(), { target: { value: "-10" } });
    fireEvent.click(screen.getByRole("button", { name: "Set chiller setpoint" }));
    await waitFor(() => expect(mocks.setTemperature).toHaveBeenCalledTimes(1));
    expect(mocks.setTemperature).toHaveBeenCalledWith("lle_chiller", -10);
  });

  it("refuses to send a setpoint outside the device's published limits", async () => {
    render(<ChillerTile snapshot={snapshot()} />);
    // 45 °C is outside details.setpoint_limits_c; the device would answer 422.
    fireEvent.change(setpointInput(), { target: { value: "45" } });
    fireEvent.click(screen.getByRole("button", { name: "Set chiller setpoint" }));
    await waitFor(() => expect(mocks.setTemperature).not.toHaveBeenCalled());
  });

  it("starts and stops the cooling", async () => {
    render(<ChillerTile snapshot={snapshot()} />);
    fireEvent.click(screen.getByRole("button", { name: "Start cooling" }));
    await waitFor(() => expect(mocks.start).toHaveBeenCalledTimes(1));
    expect(mocks.start).toHaveBeenCalledWith("lle_chiller", {
      pump_speed_rpm: 2000,
    });
    fireEvent.click(screen.getByRole("button", { name: "Stop cooling" }));
    await waitFor(() => expect(mocks.stop).toHaveBeenCalledTimes(1));
  });

  it("mirrors allowed_actions: no second start while circulating", () => {
    render(
      <ChillerTile
        snapshot={snapshot({
          equipment_status: "busy",
          activity: "running",
          allowed_actions: [
            "shutdown",
            "chiller.stop",
            "chiller.reset",
            "chiller.set_temperature",
            "chiller.set_pump_speed",
          ],
        })}
      />,
    );
    const start = screen.getByRole("button", { name: "Start cooling" });
    expect((start as HTMLButtonElement).disabled).toBe(true);
    // Retargeting a running chiller is its normal use, so Set stays live.
    expect((setpointInput() as HTMLInputElement).disabled).toBe(false);
  });

  it("keeps Stop available when the device can no longer be read", () => {
    // `unknown` = the pump readback failed. Halting circulation must never
    // depend on a readback, so the device still advertises chiller.stop.
    render(
      <ChillerTile
        snapshot={snapshot({
          equipment_status: "unknown",
          activity: "unknown",
          allowed_actions: ["shutdown", "chiller.stop"],
        })}
      />,
    );
    const stop = screen.getByRole("button", { name: "Stop cooling" });
    expect((stop as HTMLButtonElement).disabled).toBe(false);
    expect(
      (screen.getByRole("button", { name: "Start cooling" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("offers CONNECT — and says the chiller is unaffected — when the port is closed", () => {
    render(
      <ChillerTile
        snapshot={snapshot({
          equipment_status: "requires_init",
          allowed_actions: ["startup"],
          details: { hardware_state_observed: false },
        })}
      />,
    );
    expect(screen.getByRole("button", { name: "CONNECT" })).toBeTruthy();
    expect(screen.getByText(/may still be running/)).toBeTruthy();
    // No run row: there is no serial link to run anything over.
    expect(screen.queryByRole("button", { name: "Start cooling" })).toBeNull();
  });

  it("never offers a power-looking control that leaves the chiller running", () => {
    render(<ChillerTile snapshot={snapshot()} />);
    // /control/shutdown closes the port and keeps the hardware cooling, so it
    // must not appear as OFF next to the status pill.
    expect(screen.queryByRole("button", { name: "OFF" })).toBeNull();
    expect(screen.queryByRole("button", { name: "STOP" })).toBeNull();
  });

  it("locks every control when the operator is not authorized", () => {
    mocks.locked = true;
    render(<ChillerTile snapshot={snapshot()} />);
    for (const name of ["Start cooling", "Stop cooling"]) {
      expect(
        (screen.getByRole("button", { name }) as HTMLButtonElement).disabled,
      ).toBe(true);
    }
    expect((setpointInput() as HTMLInputElement).disabled).toBe(true);
  });
});
