// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { EquipmentGrid } from "./EquipmentGrid";
import { EquipmentStatusCard } from "./EquipmentStatusCard";

// The kind tiles are stubbed: this file tests which tile the grid picks, not
// what the tiles draw.
vi.mock("./RobotArmTile", () => ({ RobotArmTile: () => <div>ROBOT_ARM_TILE</div> }));
vi.mock("./LiquidHandlerTile", () => ({ LiquidHandlerTile: () => <div>LIQUID_HANDLER_TILE</div> }));
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

describe("EquipmentGrid dispatch for monitoring-only devices", () => {
  it("gives a monitoring-only robot arm the generic card instead of the xArm tile", () => {
    render(
      <EquipmentGrid
        snapshots={[snap("gibbie_ur_arm", "robot_arm", { monitoring_only: true }, "UR Arm (Gibbie)")]}
      />,
    );
    expect(screen.queryByText("ROBOT_ARM_TILE")).toBeNull();
    expect(screen.getByText("UR Arm (Gibbie)")).toBeTruthy();
    expect(screen.getByText("observed only")).toBeTruthy();
    // Nothing to gate: no lock chip, no controls of any kind.
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
  it("shows the chip for a destructive kind, and drops it when the device is monitoring-only", () => {
    const operated = render(<EquipmentStatusCard snapshot={snap("some_arm", "robot_arm", {})} />);
    expect(screen.getAllByRole("button").length).toBeGreaterThan(0);
    operated.unmount();
    render(<EquipmentStatusCard snapshot={snap("gibbie_ur_arm", "robot_arm", { monitoring_only: true })} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});
