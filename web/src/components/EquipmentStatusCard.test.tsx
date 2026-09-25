// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { EquipmentStatusCard } from "./EquipmentStatusCard";

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

const postGenericStartup = vi.fn(async (_equipmentId: string) => ({ ok: true }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  postGenericStartup: (equipmentId: string) => postGenericStartup(equipmentId),
}));

vi.mock("./CameraPlayer", () => ({
  CameraPlayer: ({ src }: { src: string }) => <div data-testid="camera-player">{src}</div>,
}));

function snapshot(over: {
  equipment_status: string;
  allowed_actions?: string[];
  required_actions?: string[];
  fetch_error?: object | null;
}): EquipmentSnapshot {
  return {
    id: "dose_every_well",
    name: "Dose Every Well",
    kind: "solid_doser",
    fetched_at: "2026-08-15T00:00:00Z",
    latency_ms: 10,
    fetch_error: over.fetch_error ?? null,
    status: {
      protocol_version: "1.1",
      equipment_id: "dose_every_well",
      equipment_name: "Dose Every Well",
      equipment_kind: "solid_doser",
      equipment_status: over.equipment_status,
      device_time: "2026-08-15T00:00:00Z",
      required_actions: over.required_actions ?? [],
      allowed_actions: over.allowed_actions ?? [],
      details: {},
      metrics: {},
      components: {},
    },
  } as unknown as EquipmentSnapshot;
}

afterEach(() => {
  cleanup();
  postGenericStartup.mockClear();
});

describe("EquipmentStatusCard generic INIT", () => {
  it("offers INIT on a requires_init device that advertises startup, and posts it", () => {
    render(
      <EquipmentStatusCard
        snapshot={snapshot({
          equipment_status: "requires_init",
          allowed_actions: ["startup"],
          required_actions: ["startup"],
        })}
      />,
    );
    const init = screen.getByRole("button", { name: /init/i });
    fireEvent.click(init);
    expect(postGenericStartup).toHaveBeenCalledWith("dose_every_well");
  });

  it("stays button-free when the device does not advertise startup", () => {
    // The device is the authority (STATUS_SPEC §6.2): the fume hood's init
    // is a sash.move, not a startup — the generic card must not invent one.
    render(
      <EquipmentStatusCard
        snapshot={snapshot({
          equipment_status: "requires_init",
          required_actions: ["sash.move"],
          allowed_actions: ["sash.move"],
        })}
      />,
    );
    expect(screen.queryByRole("button", { name: /init/i })).toBeNull();
  });

  it("does not offer INIT on an unreachable device", () => {
    render(
      <EquipmentStatusCard
        snapshot={snapshot({
          equipment_status: "requires_init",
          allowed_actions: ["startup"],
          fetch_error: { kind: "timeout", message: "unreachable" },
        })}
      />,
    );
    expect(screen.queryByRole("button", { name: /init/i })).toBeNull();
  });

  it("does not offer INIT on a ready device", () => {
    render(
      <EquipmentStatusCard
        snapshot={snapshot({ equipment_status: "ready", allowed_actions: ["startup", "shutdown"] })}
      />,
    );
    expect(screen.queryByRole("button", { name: /init/i })).toBeNull();
  });
});

describe("EquipmentStatusCard embedded camera", () => {
  function flexWithCameras() {
    const withCamera = snapshot({ equipment_status: "requires_init" });
    withCamera.id = "gibbie_flex";
    const lens = (id: string, view: string, label: string) => ({
      id, view, label, rtsp_path: "stream1",
      stream_path: `/devices/gibbie_flex/${id}`, ptz_capable: false,
    });
    withCamera.camera = {
      host: "sdl2-pc-04.tail6a1dd7.ts.net",
      onvif_port: 2020,
      rtsp_port: 554,
      transport: "mjpeg",
      lenses: [
        lens("main", "Corner Camera", "Deck"),
        lens("pipette_rgb", "Pipette Camera", "RGB"),
        lens("pipette_depth", "Pipette Camera", "Depth"),
      ],
    };
    withCamera.status.components = {
      camera: { connected: false, state: "off", message: null, last_event_at: null },
    };
    return withCamera;
  }

  it("shows the camera section unfolded, with video off until the viewer asks", () => {
    render(<EquipmentStatusCard snapshot={flexWithCameras()} />);
    expect(screen.getByText("Camera view off")).toBeTruthy();
    expect(screen.queryByTestId("camera-player")).toBeNull();
    expect(screen.getByRole("button", { name: "Corner Camera" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Show stream" }));
    expect(screen.getByTestId("camera-player").textContent).toContain("src=gibbie_flex_main");
    fireEvent.click(screen.getByRole("button", { name: "Hide stream" }));
    expect(screen.queryByTestId("camera-player")).toBeNull();
  });

  it("switches between corner and pipette cameras, and RGB and depth", () => {
    render(<EquipmentStatusCard snapshot={flexWithCameras()} />);
    expect(screen.queryByRole("group", { name: "Channel" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Pipette Camera" }));
    fireEvent.click(screen.getByRole("button", { name: "Show stream" }));
    expect(screen.getByTestId("camera-player").textContent).toContain("src=gibbie_flex_pipette_rgb");

    fireEvent.click(screen.getByRole("button", { name: "Depth" }));
    expect(screen.getByTestId("camera-player").textContent).toContain("src=gibbie_flex_pipette_depth");

    fireEvent.click(screen.getByRole("button", { name: "Corner Camera" }));
    expect(screen.getByTestId("camera-player").textContent).toContain("src=gibbie_flex_main");
    expect(screen.queryByRole("group", { name: "Channel" })).toBeNull();
  });
});

it("offers the Gibbie Flex monitor no control interface and no monitor controls", () => {
  // No Flex operator panel exists to link at (device-panels.ts explains
  // where that was checked), so the tile must not advertise one.
  const flex = snapshot({ equipment_status: "requires_init", allowed_actions: ["startup"] });
  flex.id = "gibbie_flex";
  flex.status.details = { monitoring_only: true };
  render(<EquipmentStatusCard snapshot={flex} />);
  expect(screen.queryByRole("link", { name: /control interface/i })).toBeNull();
  expect(screen.queryByRole("button", { name: /init/i })).toBeNull();
  expect(postGenericStartup).not.toHaveBeenCalled();
});
