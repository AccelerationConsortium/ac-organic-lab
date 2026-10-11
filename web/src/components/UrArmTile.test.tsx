// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { UrArmTile } from "./UrArmTile";

const auth = {
  authenticated: true,
  loading: false,
  role: "operator",
  canControl: () => auth.authenticated,
  requestLogin: vi.fn(),
};
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => auth }));

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

type Session = {
  open: boolean;
  busy?: boolean;
  manual_mode?: boolean;
  latched?: string | null;
};

// The live robot-motion envelope of 2026-10-11 (observation `ready`, arm
// session closed, nobody holding control), trimmed to what the tile reads.
function snapshot(over: {
  session?: Session | null;
  allowed?: string[];
  claimedBy?: { owner: string; session_id: string } | null;
  gripperBusy?: boolean;
  remote?: boolean | null;
  fetchError?: boolean;
} = {}): EquipmentSnapshot {
  const session = over.session === undefined ? { open: false } : over.session;
  return {
    id: "ligand_ur5e",
    name: "UR5e Arm",
    kind: "robot_arm",
    adapter: "http",
    enabled: true,
    fetched_at: "2026-10-11T20:37:00Z",
    latency_ms: 12,
    fetch_error: over.fetchError
      ? { kind: "timeout", message: "timed out" }
      : null,
    pill: { link_label: "Open", link_href: "/ur5e/web/" },
    status: {
      protocol_version: "1.2",
      equipment_id: "ligand_ur5e",
      equipment_name: "UR5e Arm",
      equipment_kind: "robot_arm",
      equipment_status: "ready",
      activity: "idle",
      message: "Read-only observation: program stopped",
      device_time: "2026-10-11T20:37:00Z",
      required_actions: [],
      allowed_actions: over.allowed ?? ["control.stop", "connect", "gripper.open", "gripper.close", "gripper.move"],
      metrics: {},
      components: {
        controller: { connected: true, state: "running" },
        safety: { connected: true, state: "normal" },
        program: { connected: true, state: "stopped" },
        gripper: { connected: true, state: "enabled" },
      },
      last_error: null,
      details: {
        robotmode: "RUNNING",
        safetystatus: "NORMAL",
        program_state: "STOPPED",
        remote_control: over.remote === undefined ? true : over.remote,
        operational_mode: "AUTOMATIC",
        monitoring_only: false,
        control_enabled: true,
        claimed_by: over.claimedBy ?? null,
        control_session: session && {
          arm_control: true,
          mode: "motion",
          busy: false,
          manual_mode: false,
          latched: null,
          ...session,
        },
        gripper: {
          model: "robotiq_2f140",
          name: "Robotiq 2F-140",
          busy: over.gripperBusy ?? false,
          state: "enabled",
          opening_mm: 69.7,
        },
        telemetry: {
          tcp_force: { force_magnitude_n: 10.8, frame: "base" },
        },
      },
    },
  } as unknown as EquipmentSnapshot;
}

afterEach(() => {
  cleanup();
  auth.authenticated = true;
  auth.requestLogin.mockClear();
  postArmConnect.mockClear();
  postArmDisconnect.mockClear();
  postArmStop.mockClear();
  postArmClear.mockClear();
});

const button = (name: string | RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;

describe("UrArmTile with the arm session closed", () => {
  it("offers INIT (connect) and greys STOP: nothing is moving before Connect", () => {
    render(<UrArmTile snapshot={snapshot()} />);
    const init = button(/INIT/);
    expect(init.disabled).toBe(false);
    expect(init.title).toMatch(/click to connect/);
    const stop = button("STOP");
    expect(stop.disabled).toBe(true);
    expect(stop.title).toMatch(/Nothing to stop/);
    // No CLEAR: Clear errors is holder-only on this device (the panel has it).
    expect(screen.queryByRole("button", { name: /clear/i })).toBeNull();
  });

  it("connects through the generic arm action", () => {
    render(<UrArmTile snapshot={snapshot()} />);
    fireEvent.click(button(/INIT/));
    expect(postArmConnect).toHaveBeenCalledWith("ligand_ur5e");
    expect(postArmDisconnect).not.toHaveBeenCalled();
  });

  it("greys INIT when the device does not offer connect, and says why", () => {
    render(<UrArmTile snapshot={snapshot({ allowed: ["control.stop"], remote: false })} />);
    const init = button(/INIT/);
    expect(init.disabled).toBe(true);
    expect(init.title).toMatch(/Remote Control/);
    expect(screen.getByText("local")).toBeTruthy();
  });

  it("names the holder when someone else's claim blocks connect", () => {
    render(
      <UrArmTile
        snapshot={snapshot({ allowed: ["control.stop"], claimedBy: { owner: "someone@lab", session_id: "x" } })}
      />,
    );
    expect(button(/INIT/).title).toMatch(/held by someone@lab/);
    expect(screen.getByText("someone@lab")).toBeTruthy();
  });

  it("still offers STOP while the gripper is moving on its own socket", () => {
    render(<UrArmTile snapshot={snapshot({ gripperBusy: true })} />);
    expect(button("STOP").disabled).toBe(false);
    fireEvent.click(button("STOP"));
    expect(postArmStop).toHaveBeenCalledWith("ligand_ur5e");
    expect(screen.getByText("enabled · moving")).toBeTruthy();
  });

  it("shows the controller, session, safety, force and gripper readings", () => {
    render(<UrArmTile snapshot={snapshot()} />);
    for (const label of ["Arm", "Safety", "Gripper", "running", "closed", "nobody", "normal", "remote", "10.8 N", "69.7 mm"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });
});

describe("UrArmTile with the arm session open", () => {
  it("shows ON, offers STOP, and asks before disconnecting", () => {
    render(<UrArmTile snapshot={snapshot({ session: { open: true }, allowed: ["control.stop", "disconnect", "arm.move_joints"] })} />);
    const on = button(/ON/);
    expect(on.disabled).toBe(false);
    expect(button("STOP").disabled).toBe(false);
    fireEvent.click(on);
    expect(postArmDisconnect).not.toHaveBeenCalled();
    fireEvent.click(button("Yes"));
    expect(postArmDisconnect).toHaveBeenCalledWith("ligand_ur5e");
    expect(screen.getByText("open")).toBeTruthy();
  });

  it("does not disconnect while a move is in progress", () => {
    render(<UrArmTile snapshot={snapshot({ session: { open: true, busy: true }, allowed: ["control.stop"] })} />);
    const on = button(/ON/);
    expect(on.disabled).toBe(true);
    expect(on.title).toMatch(/Stop before disconnecting/);
    expect(screen.getByText("open · moving")).toBeTruthy();
    expect(button("STOP").disabled).toBe(false);
  });

  it("flags a latched stop and points at the panel's Clear errors", () => {
    render(
      <UrArmTile
        snapshot={snapshot({ session: { open: true, latched: "Stop requested" }, allowed: ["control.stop", "disconnect", "control.reset"] })}
      />,
    );
    expect(screen.getByText("latched")).toBeTruthy();
  });
});

describe("UrArmTile gating", () => {
  it("links to the edge-routed device panel in a new tab", () => {
    render(<UrArmTile snapshot={snapshot()} />);
    const link = screen.getByRole("link", { name: "Open control panel ↗" });
    expect(link.getAttribute("href")).toBe("/ur5e/web/");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("prompts SDL2 login when the panel link is clicked while signed out, and greys the controls", () => {
    auth.authenticated = false;
    render(<UrArmTile snapshot={snapshot({ session: { open: true }, allowed: ["control.stop", "disconnect"] })} />);
    fireEvent.click(screen.getByRole("link", { name: "Open control panel ↗" }));
    expect(auth.requestLogin).toHaveBeenCalledOnce();
    expect(button(/ON/).disabled).toBe(true);
    expect(button("STOP").disabled).toBe(true);
    expect(button("STOP").title).toBe("Sign in to control");
  });

  it("greys everything and reports unknown state after a failed poll", () => {
    render(<UrArmTile snapshot={snapshot({ fetchError: true })} />);
    expect(button(/INIT/).disabled).toBe(true);
    expect(button("STOP").disabled).toBe(true);
    // Mode, session and safety all read unknown without a poll.
    expect(screen.getAllByText("unknown").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText(/Service unreachable/)).toBeTruthy();
  });
});
