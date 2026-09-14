// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EquipmentSnapshot } from "@/types/api";
import { isMonitoringOnly } from "@/lib/tile-policy";
import { UrMonitorTile } from "./UrMonitorTile";

const auth = { authenticated: true, loading: false, role: "operator", canControl: () => auth.authenticated, requestLogin: vi.fn() };
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => auth }));

function snapshot(): EquipmentSnapshot {
  return {
    id: "lle_ur5_arm", name: "UR5-CB3 Arm", kind: "robot_arm", adapter: "http",
    enabled: true, activity: "running", activity_source: "device", health: "healthy",
    mode: "production", simulated: false, fetched_at: new Date().toISOString(),
    status: {
      protocol_version: "1.2", equipment_id: "lle_ur5_arm", equipment_name: "UR5-CB3 Arm",
      equipment_kind: "robot_arm", equipment_status: "busy", activity: "running",
      device_time: new Date().toISOString(), allowed_actions: [], required_actions: [],
      metrics: {}, components: {}, last_error: null, message: "Observed only",
      details: { monitoring_only: true, robotmode: "RUNNING", safetystatus: "NORMAL", program_state: "PLAYING example.urp" },
    },
  };
}

afterEach(() => { cleanup(); auth.authenticated = true; auth.requestLogin.mockClear(); });

describe("read-only UR arms", () => {
  it("links only the Ligand UR5e to its SDL2-authenticated workspace in a new tab", () => {
    const value = { ...snapshot(), id: "ligand_ur5e" };
    render(<UrMonitorTile snapshot={value} />);
    const link = screen.getByRole("link", { name: "Open control panel ↗" });
    expect(link.getAttribute("href")).toBe("/utils/robot_motion");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.className).toContain("border-orange-300");
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByText("Read-only monitoring")).toBeTruthy();
  });

  it("prompts SDL2 login when the UR5e link is clicked while signed out", () => {
    auth.authenticated = false;
    render(<UrMonitorTile snapshot={{ ...snapshot(), id: "ligand_ur5e" }} />);
    fireEvent.click(screen.getByRole("link", { name: "Open control panel ↗" }));
    expect(auth.requestLogin).toHaveBeenCalledOnce();
  });

  it("does not add a workspace link to the Gibbie arm", () => {
    render(<UrMonitorTile snapshot={{ ...snapshot(), id: "gibbie_ur_arm" }} />);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });
  it("shows only the three status readings and no robot controls", () => {
    render(<UrMonitorTile snapshot={snapshot()} />);
    for (const label of ["Mode", "Safety", "Program", "RUNNING", "NORMAL", "PLAYING", "Read-only monitoring"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.queryByText(/example\.urp/)).toBeNull();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
  });

  it("does not show cached readings after a failed poll", () => {
    const value = snapshot();
    value.fetch_error = { kind: "timeout", message: "timed out" } as EquipmentSnapshot["fetch_error"];
    render(<UrMonitorTile snapshot={value} />);
    expect(screen.getByText("Aggregator could not reach device")).toBeTruthy();
    for (const text of ["RUNNING", "NORMAL", "PLAYING"]) expect(screen.queryByText(text)).toBeNull();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("handles older component-only observations", () => {
    const value = snapshot();
    value.status.details = {};
    value.status.components = {
      controller: { connected: true, state: "running" },
      safety: { connected: true, state: "normal" },
      program: { connected: false, state: "stopped" },
    };
    render(<UrMonitorTile snapshot={value} />);
    expect(screen.getByText("normal")).toBeTruthy();
    expect(screen.getByText("stopped")).toBeTruthy();
  });

  it("keeps monitoring identities and the uncommissioned prototype read-only when flags are missing or false", () => {
    for (const id of ["lle_ur5_arm", "gibbie_ur_arm", "ligand_ur5e"]) {
      expect(isMonitoringOnly({ id })).toBe(true);
      expect(isMonitoringOnly({ id, status: { details: { monitoring_only: false } } })).toBe(true);
    }
    expect(isMonitoringOnly({ id: "xarm_translocation" })).toBe(false);
  });
});
