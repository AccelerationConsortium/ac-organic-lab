// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EquipmentSnapshot } from "@/types/api";
import { PlateWeigherTile } from "./PlateWeigherTile";

const mocks = vi.hoisted(() => ({
  locked: false,
  send: vi.fn<(...args: unknown[]) => Promise<Record<string, unknown>>>(async () => ({ ok: true })),
  live: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => { throw new Error("no live"); }),
}));
vi.mock("@/lib/use-control-lock", () => ({
  useControlLock: () => ({ locked: mocks.locked, countdown: 0, toggle: vi.fn() }),
}));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<object>()),
  postPlateWeigherAction: mocks.send,
  getEquipmentStatus: mocks.live,
}));

const READY_ACTIONS = ["shutdown", "read", "tare", "raise", "lower", "open_lid", "close_lid", "park", "load_plate", "unload_plate"];

function snapshot(over: Record<string, unknown> = {}): EquipmentSnapshot {
  const {
    equipment_status = "ready" as string,
    allowed_actions = READY_ACTIONS,
    details = {},
    components = {},
    metrics = {},
    message = "Plate at weighing",
  } = over as Record<string, never>;
  return {
    id: "flex_plate_weigher",
    name: "Flex Plate Weigher",
    kind: "other",
    activity: "idle",
    fetched_at: new Date().toISOString(),
    latency_ms: 12,
    fetch_error: null,
    status: {
      equipment_status,
      activity: "idle",
      allowed_actions,
      required_actions: equipment_status === "requires_init" ? ["startup"] : [],
      message,
      metrics,
      components: {
        balance: { connected: true, state: "ready", message: null },
        lift: { connected: true, state: "ready", message: "Plate at weighing" },
        lid: { connected: true, state: "open", message: null },
        ...(components as object),
      },
      details: {
        balance: { driver: "sartorius_sbi", units: "g", last_mass_g: 1.2345, last_stable: true, condition: null },
        lift: { driver: "pca9685_servo", poses_deg: { raised: -80, weighing: 70, install: -90 }, plate_angle: 70, lid_angle: 22 },
        lift_pose: "weighing",
        lid: { driver: "pca9685_servo", open: true },
        claimed_by: null,
        ...(details as object),
      },
    },
  } as unknown as EquipmentSnapshot;
}

const button = (name: string | RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;

afterEach(() => {
  cleanup();
  mocks.send.mockReset();
  mocks.send.mockResolvedValue({ ok: true });
  mocks.live.mockReset();
  mocks.live.mockRejectedValue(new Error("no live"));
  mocks.locked = false;
});

describe("PlateWeigherTile", () => {
  it("shows the last mass in the platebalance mass box with its stability word", () => {
    render(<PlateWeigherTile snapshot={snapshot()} />);
    expect(screen.getByLabelText("Last measured weight").textContent).toContain("1.2345");
    expect(screen.getByText("Stable")).toBeTruthy();
  });

  it("renders underload as Low instead of a number", () => {
    render(
      <PlateWeigherTile
        snapshot={snapshot({
          details: { balance: { driver: "sartorius_sbi", units: "g", last_mass_g: null, last_stable: null, condition: "underload" } },
        })}
      />,
    );
    expect(screen.getByLabelText("Last measured weight").textContent).toContain("Low");
    expect(screen.getByText("Low — nothing on the pan")).toBeTruthy();
  });

  it("reads with stable=true by default and stable=false when the wait is switched off", async () => {
    render(<PlateWeigherTile snapshot={snapshot()} />);
    fireEvent.click(button("Weight"));
    await waitFor(() => expect(mocks.send).toHaveBeenCalledTimes(1));
    expect(mocks.send).toHaveBeenLastCalledWith("flex_plate_weigher", "read", { stable: true });
    fireEvent.click(screen.getByLabelText(/wait until stable/));
    fireEvent.click(button("Weight"));
    await waitFor(() => expect(mocks.send).toHaveBeenCalledTimes(2));
    expect(mocks.send).toHaveBeenLastCalledWith("flex_plate_weigher", "read", { stable: false });
  });

  it("shows the reading the device returns", async () => {
    mocks.send.mockResolvedValue({ ok: true, mass_g: 0.5012, stable: true });
    render(<PlateWeigherTile snapshot={snapshot()} />);
    fireEvent.click(button("Weight"));
    await screen.findByText("0.5012 g · stable");
  });

  it("tares and parks through the verb routes", async () => {
    render(<PlateWeigherTile snapshot={snapshot()} />);
    fireEvent.click(button("Tare"));
    await waitFor(() => expect(mocks.send).toHaveBeenLastCalledWith("flex_plate_weigher", "tare", {}));
    fireEvent.click(button("Park"));
    await waitFor(() => expect(mocks.send).toHaveBeenLastCalledWith("flex_plate_weigher", "park", {}));
  });

  it("drives the lift and lid as position pills, lit at the current pose", async () => {
    render(<PlateWeigherTile snapshot={snapshot()} />);
    const weighing = button("Lower the plate onto the pan");
    expect(weighing.getAttribute("aria-pressed")).toBe("true");
    expect(button("Open the lid").getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(button("Raise the plate off the pan"));
    await waitFor(() => expect(mocks.send).toHaveBeenLastCalledWith("flex_plate_weigher", "raise", {}));
    fireEvent.click(button("Close the lid"));
    await waitFor(() => expect(mocks.send).toHaveBeenLastCalledWith("flex_plate_weigher", "close_lid", {}));
  });

  it("offers no Zero on a balance that does not advertise it, and no lid pills without a lid", () => {
    render(
      <PlateWeigherTile
        snapshot={snapshot({
          components: { lid: undefined },
          details: { lid: undefined },
        })}
      />,
    );
    expect(screen.queryByRole("button", { name: "Zero" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Open the lid" })).toBeNull();
  });

  it("mirrors allowed_actions: balance verbs disabled while the lift moves", () => {
    render(
      <PlateWeigherTile
        snapshot={snapshot({ equipment_status: "busy", allowed_actions: ["stop", "shutdown"] })}
      />,
    );
    expect(button("Weight").disabled).toBe(true);
    expect(button("Tare").disabled).toBe(true);
    expect(button("Raise the plate off the pan").disabled).toBe(true);
    expect(button("STOP").disabled).toBe(false);
  });

  it("offers INIT on requires_init and sends startup", async () => {
    render(
      <PlateWeigherTile
        snapshot={snapshot({
          equipment_status: "requires_init",
          allowed_actions: ["startup"],
          message: "Balance not connected; lift: Servo positions unknown",
          components: { balance: { connected: false, state: "disconnected", message: "Not connected; startup connects" } },
        })}
      />,
    );
    expect(screen.getByText("Balance not connected")).toBeTruthy();
    fireEvent.click(button("INIT"));
    await waitFor(() => expect(mocks.send).toHaveBeenLastCalledWith("flex_plate_weigher", "startup", {}));
    expect(button("Weight").disabled).toBe(true);
  });

  it("disables every control and names the holder while a claim is held", () => {
    render(
      <PlateWeigherTile
        snapshot={snapshot({
          details: { claimed_by: { owner: "agent:dose-gravimetric", session_id: "s1", expires_at: "2030-01-01T00:00:00Z" } },
        })}
      />,
    );
    expect(screen.getByText("In use by agent:dose-gravimetric")).toBeTruthy();
    expect(button("Weight").disabled).toBe(true);
    expect(button("Park").disabled).toBe(true);
  });

  it("locks every control when the operator is not authorized", () => {
    mocks.locked = true;
    render(<PlateWeigherTile snapshot={snapshot()} />);
    for (const name of ["Weight", "Tare", "Park", "Raise the plate off the pan"]) {
      expect(button(name).disabled).toBe(true);
    }
  });

  it("explains a lid interlock 412 with the verb that clears it", async () => {
    const { ApiError } = await import("@/lib/api");
    mocks.send.mockRejectedValue(
      new ApiError(412, "Precondition Failed", { detail: "Lid is closed.", required: "open_lid" }, "/x", null),
    );
    render(<PlateWeigherTile snapshot={snapshot()} />);
    fireEvent.click(button("Raise the plate off the pan"));
    await waitFor(() => expect(mocks.send).toHaveBeenCalledTimes(1));
    // The refusal lives in the standard amber badge; open its popover.
    fireEvent.click(await screen.findByRole("button", { name: "Action error details" }));
    await screen.findByText(/Do "Open lid" first/);
  });
});
