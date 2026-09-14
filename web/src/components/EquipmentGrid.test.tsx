// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { EquipmentGrid } from "./EquipmentGrid";
import { EquipmentStatusCard } from "./EquipmentStatusCard";

// The kind tiles are stubbed: this file tests which tile the grid picks, not
// what the tiles draw.
vi.mock("./XprBalanceTile", () => ({ XprBalanceTile: () => <div>XPR_TILE</div> }));
vi.mock("./RobotArmTile", () => ({ RobotArmTile: () => <div>ROBOT_ARM_TILE</div> }));
vi.mock("./LiquidHandlerTile", () => ({ LiquidHandlerTile: () => <div>LIQUID_HANDLER_TILE</div> }));
vi.mock("./HplcMonitorTile", () => ({ HplcMonitorTile: () => <div>HPLC_MONITOR_TILE</div> }));
vi.mock("./HplcTile", () => ({ HplcTile: () => <div>HPLC_CONTROL_TILE</div> }));
vi.mock("@/lib/use-control-lock", () => ({
  useControlLock: () => ({
    locked: true,
    noAccess: false,
    countdown: 0,
    unlock: vi.fn(),
    lock: vi.fn(),
    toggle: vi.fn(),
  }),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  postGenericStartup: vi.fn(),
}));

function snap(
  id: string,
  kind: string,
  details: Record<string, unknown>,
  name = id,
): EquipmentSnapshot {
  return {
    id,
    name,
    kind,
    fetched_at: "2026-09-06T00:00:00Z",
    latency_ms: 10,
    fetch_error: null,
    tile: { w: 2, h: 1 },
    status: {
      protocol_version: "1.2",
      equipment_id: id,
      equipment_name: name,
      equipment_kind: kind,
      equipment_status: "ready",
      activity: "idle",
      message: "observed only",
      device_time: "2026-09-06T00:00:00Z",
      required_actions: [],
      allowed_actions: [],
      details,
      metrics: {},
      components: { link: { connected: true, state: "up" } },
    },
  } as unknown as EquipmentSnapshot;
}

afterEach(() => cleanup());

it("preserves whole and half tile heights as minimum card heights", () => {
  const snapshots = [1, 2, 2.5].map((h) => ({
    ...snap(`robot_${h}`, "robot_arm", {}),
    tile: { w: 2, h },
  }));
  render(<EquipmentGrid snapshots={snapshots} />);
  expect(screen.getAllByText("ROBOT_ARM_TILE").map((tile) => tile.parentElement?.style.minHeight))
    .toEqual(["220px", "568px", "452px"]);
});

describe("EquipmentGrid dispatch for monitoring-only devices", () => {
  it("keeps the Process Chemistry HPLC on its read-only tile even without monitoring details", () => {
    const { rerender } = render(<EquipmentGrid snapshots={[snap("lle_hplc", "hplc", { monitoring_only: true })]} />);
    expect(screen.getByText("HPLC_MONITOR_TILE")).toBeTruthy();
    const failed = snap("lle_hplc", "hplc", {});
    failed.fetch_error = { kind: "timeout", message: "timed out" } as EquipmentSnapshot["fetch_error"];
    rerender(<EquipmentGrid snapshots={[failed]} />);
    expect(screen.getByText("HPLC_MONITOR_TILE")).toBeTruthy();
    expect(screen.queryByText("HPLC_CONTROL_TILE")).toBeNull();
  });

  it("leaves the operated UPLC tile and other generic HPLC monitors unchanged", () => {
    render(<EquipmentGrid snapshots={[
      snap("uplc", "hplc", {}),
      snap("another_monitor", "hplc", { monitoring_only: true }, "Other HPLC monitor"),
    ]} />);
    expect(screen.getAllByText("HPLC_CONTROL_TILE")).toHaveLength(1);
    expect(screen.getByText("Other HPLC monitor")).toBeTruthy();
    expect(screen.queryByText("HPLC_MONITOR_TILE")).toBeNull();
  });

  it("gives the Gibbie UR arm a compact read-only card instead of the xArm tile", () => {
    render(
      <EquipmentGrid
        snapshots={[snap("gibbie_ur_arm", "robot_arm", { monitoring_only: true }, "UR Arm (Gibbie)")]}
      />,
    );
    expect(screen.queryByText("ROBOT_ARM_TILE")).toBeNull();
    expect(screen.getByText("UR Arm (Gibbie)")).toBeTruthy();
    expect(screen.getByText("observed only")).toBeTruthy();
    expect(screen.getByText("Read-only monitoring")).toBeTruthy();
    // Nothing to gate: no lock chip, no controls of any kind.
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it.each(["lle_ur5_arm", "gibbie_ur_arm", "ligand_ur5e"])("never exposes xArm controls for %s after a failed poll", (id) => {
    const failed = snap(id, "robot_arm", {});
    failed.fetch_error = { kind: "timeout", message: "timed out" } as EquipmentSnapshot["fetch_error"];
    render(<EquipmentGrid snapshots={[failed]} />);
    expect(screen.queryByText("ROBOT_ARM_TILE")).toBeNull();
    expect(screen.getByText("Read-only monitoring")).toBeTruthy();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("gives a monitoring-only liquid handler the generic card instead of the OT-2 tile", () => {
    render(
      <EquipmentGrid
        snapshots={[snap("gibbie_flex", "liquid_handler", { monitoring_only: true }, "Opentrons Flex (Gibbie)")]}
      />,
    );
    expect(screen.queryByText("LIQUID_HANDLER_TILE")).toBeNull();
    expect(screen.getByText("Opentrons Flex (Gibbie)")).toBeTruthy();
  });

  it("still gives an operated robot arm and liquid handler their kind tiles", () => {
    render(
      <EquipmentGrid
        snapshots={[snap("xarm_translocation", "robot_arm", {}), snap("ot2_hte", "liquid_handler", {})]}
      />,
    );
    expect(screen.getByText("ROBOT_ARM_TILE")).toBeTruthy();
    expect(screen.getByText("LIQUID_HANDLER_TILE")).toBeTruthy();
  });

  it("ignores a monitoring_only value that is not literally true", () => {
    render(<EquipmentGrid snapshots={[snap("xarm_translocation", "robot_arm", { monitoring_only: "yes" })]} />);
    expect(screen.getByText("ROBOT_ARM_TILE")).toBeTruthy();
  });
});

describe("EquipmentStatusCard lock chip", () => {
  it("does not offer generic INIT to a read-only device even if an action is advertised", () => {
    const monitored = snap("monitor", "robot_arm", { monitoring_only: true });
    monitored.status.equipment_status = "requires_init";
    monitored.status.allowed_actions = ["startup"];
    monitored.status.required_actions = ["startup"];
    render(<EquipmentStatusCard snapshot={monitored} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
  it("shows the chip for a destructive kind, and drops it when the device is monitoring-only", () => {
    const operated = render(<EquipmentStatusCard snapshot={snap("some_arm", "robot_arm", {})} />);
    expect(screen.getAllByRole("button").length).toBeGreaterThan(0);
    operated.unmount();
    render(<EquipmentStatusCard snapshot={snap("gibbie_ur_arm", "robot_arm", { monitoring_only: true })} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});

it("shows XPR controls only for the control service, not its reachability monitor", () => {
  const { rerender } = render(<EquipmentGrid snapshots={[snap("lle_xpr_balance", "other", { monitoring_only: true })]} />);
  expect(screen.queryByText("XPR_TILE")).toBeNull();
  rerender(<EquipmentGrid snapshots={[snap("lle_xpr_balance", "other", {})]} />);
  expect(screen.getByText("XPR_TILE")).toBeTruthy();
});
