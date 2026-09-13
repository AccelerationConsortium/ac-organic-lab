// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { CameraTile } from "./CameraTile";

// View-only visitor: the stream toggle is a view-side control and must work
// without any role on the camera.
vi.mock("@/lib/user-auth", () => ({
  useUserAuth: () => ({ authenticated: false, canControl: () => false, loading: false, identity: null }),
}));
vi.mock("@/lib/api", () => ({
  cancelRecording: vi.fn(),
  deletePreset: vi.fn(),
  gotoPreset: vi.fn(),
  mediaUrlForBrowser: (u: string) => u,
  postPtz: vi.fn(),
  savePreset: vi.fn(),
  setPrivacy: vi.fn(),
  setStreaming: vi.fn(),
  startRecording: vi.fn(),
  startRolling: vi.fn(),
  stopRecording: vi.fn(),
  stopRolling: vi.fn(),
  takeSnapshot: vi.fn(),
}));
// Stub the player: jsdom has no MediaSource/WebSocket worth exercising here.
// What matters is the `disabled` prop, because that is what keeps MsePlayer /
// WebRtcPlayer from opening the WebSocket (both early-return on `disabled`).
vi.mock("./CameraPlayer", () => ({
  CameraPlayer: ({ disabled, src }: { disabled?: boolean; src: string | null }) => (
    <div data-testid="player" data-disabled={String(Boolean(disabled))} data-src={src ?? ""} />
  ),
}));

const snapshot = {
  id: "cam_hte_tapo_c245",
  name: "HTE Camera",
  kind: "camera",
  adapter: "http",
  fetched_at: "2026-09-12T20:00:00Z",
  latency_ms: 40,
  fetch_error: null,
  pill: {},
  camera: { lenses: [{ id: "wide", label: "Wide", ptz_capable: true }] },
  status: {
    protocol_version: "1.0",
    equipment_id: "cam_hte_tapo_c245",
    equipment_name: "HTE Camera",
    equipment_kind: "camera",
    equipment_status: "ready",
    device_time: "2026-09-12T20:00:00Z",
    components: {},
    metrics: {},
    allowed_actions: [],
    required_actions: [],
    details: {
      lenses: [{ id: "wide", label: "Wide", mse_url: "/streams/api/ws?src=cam_hte_tapo_c245_wide", stream_connected: true }],
      presets: [],
      streaming_enabled: true,
      privacy_mode: false,
      onvif_reachable: true,
      tapo_reachable: true,
    },
  },
} as unknown as EquipmentSnapshot;

function renderTile() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <CameraTile snapshot={snapshot} />
    </QueryClientProvider>,
  );
}

afterEach(cleanup);

describe("CameraTile stream is opt-in", () => {
  // The cameras are on the campus Wi-Fi and every open stream crosses gaia's
  // own radio twice (ingest + viewer). A detail page left open on a bench PC
  // must therefore not stream until someone asks. See AGENTS.md §4.
  it("mounts the player disabled and offers a Show-stream control", () => {
    renderTile();
    const player = screen.getByTestId("player");
    expect(player.getAttribute("data-disabled")).toBe("true");
    // The stream URL is wired but not connected: `disabled` is the gate.
    expect(player.getAttribute("data-src")).toContain("cam_hte_tapo_c245_wide");
    // Banner button + click-to-start overlay share the title; both are offers.
    expect(screen.getAllByTitle("Show camera stream").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByTitle("Hide camera stream")).toBeNull();
    expect(screen.getByText("Stream off")).toBeTruthy();
  });

  it("enables the player only after the viewer turns it on, and can turn it off again", () => {
    renderTile();
    fireEvent.click(screen.getAllByTitle("Show camera stream")[0]);
    expect(screen.getByTestId("player").getAttribute("data-disabled")).toBe("false");
    expect(screen.queryByText("Stream off")).toBeNull();
    const hide = screen.getByTitle("Hide camera stream");
    fireEvent.click(hide);
    expect(screen.getByTestId("player").getAttribute("data-disabled")).toBe("true");
    expect(screen.getByText("Stream off")).toBeTruthy();
  });

  it("keeps the player disabled while on if the camera itself has streaming off", () => {
    const off = {
      ...snapshot,
      status: { ...snapshot.status, details: { ...(snapshot.status as { details: object }).details, streaming_enabled: false } },
    } as unknown as EquipmentSnapshot;
    render(
      <QueryClientProvider client={new QueryClient()}>
        <CameraTile snapshot={off} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getAllByTitle("Show camera stream")[0]);
    // Viewer opted in, but the device-side flag still wins — no WebSocket.
    expect(screen.getByTestId("player").getAttribute("data-disabled")).toBe("true");
  });
});
