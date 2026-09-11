// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { RobotArmTile } from "./RobotArmTile";

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

const postArmConnect = vi.fn(async (_id: string) => ({ ok: true }));
const postArmDisconnect = vi.fn(async (_id: string) => ({ ok: true }));
const postArmStop = vi.fn(async (_id: string) => ({ ok: true }));
const postArmClear = vi.fn(async (_id: string) => ({ ok: true }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  postArmConnect: (id: string) => postArmConnect(id),
  postArmDisconnect: (id: string) => postArmDisconnect(id),
  postArmStop: (id: string) => postArmStop(id),
  postArmClear: (id: string) => postArmClear(id),
}));

type Comp = { connected?: boolean; state: string; message?: string | null };

function snapshot(over: {
  id?: string;
  name?: string;
  equipment_status?: string;
  components: Record<string, Comp>;
  metrics?: Record<string, { value: number; unit: string }>;
  details?: Record<string, unknown>;
}): EquipmentSnapshot {
  return {
    id: over.id ?? "xarm_translocation",
    name: over.name ?? "UFactory xArm5",
    kind: "robot_arm",
    fetched_at: "2026-09-09T00:00:00Z",
    latency_ms: 10,
    fetch_error: null,
    status: {
      protocol_version: "1.2",
      equipment_id: over.id ?? "xarm_translocation",
      equipment_name: over.name ?? "UFactory xArm5",
      equipment_kind: "robot_arm",
      equipment_status: over.equipment_status ?? "ready",
      device_time: "2026-09-09T00:00:00Z",
      required_actions: [],
      allowed_actions: [],
      details: over.details ?? {},
      metrics: over.metrics ?? {},
      components: over.components,
    },
  } as unknown as EquipmentSnapshot;
}

// The xArm5: reports arm + gripper + track + a force-torque sensor, and the
// track/gripper metrics + gripper_config the tile grew up reading.
function xarmSnapshot(): EquipmentSnapshot {
  return snapshot({
    id: "xarm_translocation",
    name: "UFactory xArm5",
    components: {
      arm: { connected: true, state: "enabled" },
      gripper: { connected: true, state: "open" },
      track: { connected: true, state: "idle" },
      force_torque: { connected: true, state: "disabled" },
    },
    metrics: {
      tcp_speed: { value: 120, unit: "mm/s" },
      angle_speed: { value: 30, unit: "deg/s" },
      track_position: { value: 250, unit: "mm" },
    },
    details: {
      connection_details: {
        gripper_config: { force: 20, stroke_range: { min: 71, max: 150 } },
      },
      motion_graph: { rail_location_name: "deck" },
    },
  });
}

// The MG400: bolted down (no track), a suction cup (no jaw stroke, no FT), so
// it reports only arm + gripper and none of the xArm-only metrics.
function mg400Snapshot(over?: { equipment_status?: string }): EquipmentSnapshot {
  return snapshot({
    id: "dobot_mg400",
    name: "Dobot MG400",
    equipment_status: over?.equipment_status ?? "ready",
    components: {
      arm: { connected: true, state: "enabled" },
      gripper: { connected: true, state: "empty", message: "suction cup; unconfirmed" },
    },
    metrics: {},
    details: { motion_graph: { graph_mode: "strict", current_node: "home" } },
  });
}

afterEach(() => {
  cleanup();
  postArmConnect.mockClear();
  postArmDisconnect.mockClear();
  postArmStop.mockClear();
  postArmClear.mockClear();
});

describe("RobotArmTile with the xArm shape", () => {
  it("renders all three rows and their xArm-specific metric pills", () => {
    render(<RobotArmTile snapshot={xarmSnapshot()} />);
    expect(screen.getByText("Arm")).toBeTruthy();
    expect(screen.getByText("Gripper")).toBeTruthy();
    expect(screen.getByText("Track")).toBeTruthy();
    // xArm-only metric pills present.
    expect(screen.getByText("TCP")).toBeTruthy();
    expect(screen.getByText("Ang")).toBeTruthy();
    // Configured stroke range (gripper_config) and the rail preset.
    expect(screen.getByText("71–150 mm")).toBeTruthy();
    expect(screen.getByText("deck")).toBeTruthy();
  });
});

describe("RobotArmTile with the MG400 shape", () => {
  it("omits the whole Track row when no track component is published", () => {
    render(<RobotArmTile snapshot={mg400Snapshot()} />);
    expect(screen.getByText("Arm")).toBeTruthy();
    expect(screen.getByText("Gripper")).toBeTruthy();
    expect(screen.queryByText("Track")).toBeNull();
  });

  it("shows the arm + cup states", () => {
    render(<RobotArmTile snapshot={mg400Snapshot()} />);
    expect(screen.getByText("enabled")).toBeTruthy();
    expect(screen.getByText("empty")).toBeTruthy();
  });

  it("omits xArm-only pills the device does not publish", () => {
    render(<RobotArmTile snapshot={mg400Snapshot()} />);
    // No tcp_speed / angle_speed metrics, no gripper_config, no FT sensor.
    expect(screen.queryByText("TCP")).toBeNull();
    expect(screen.queryByText("Ang")).toBeNull();
    expect(screen.queryByText("Range")).toBeNull();
    expect(screen.queryByText("Stroke")).toBeNull();
    expect(screen.queryByText("Force")).toBeNull();
  });
});

describe("RobotArmTile INIT control", () => {
  it("connects the controller when INIT is clicked while disconnected", () => {
    render(<RobotArmTile snapshot={mg400Snapshot({ equipment_status: "requires_init" })} />);
    fireEvent.click(screen.getByRole("button", { name: "INIT" }));
    expect(postArmConnect).toHaveBeenCalledWith("dobot_mg400");
  });
});
