// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { TileShell } from "./TileShell";
import { ShakerTile } from "./ShakerTile";

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

const degradedSnapshot = {
  id: "torrey-pines-shaker",
  name: "Torrey Pines shaker",
  kind: "shaker",
  fetched_at: "2026-07-21T12:00:00Z",
  latency_ms: 12,
  status: {
    protocol_version: "1.1",
    equipment_id: "torrey-pines-shaker",
    equipment_name: "Torrey Pines shaker",
    equipment_kind: "shaker",
    equipment_status: "degraded",
    device_time: "2026-07-21T12:00:00Z",
    required_actions: [],
    allowed_actions: ["shake.start", "shake.stop"],
    details: {},
    metrics: {},
    components: {
      heater: {
        connected: false,
        state: "disconnected",
        message: "Calibration RTD is unavailable",
      },
      motor: {
        connected: true,
        state: "shaking",
        message: "Shaking at level 5",
      },
    },
  },
} as unknown as EquipmentSnapshot;

afterEach(cleanup);

describe("TileShell status presentation", () => {
  it("shows busy activity separately from degraded health with component details", () => {
    render(
      <TileShell snapshot={degradedSnapshot} displayStatus="busy" headerRight={null}>
        <div>Tile body</div>
      </TileShell>,
    );

    expect(screen.getByText("Busy")).toBeTruthy();
    expect(screen.getByText("Degraded")).toBeTruthy();

    const health = screen.getByRole("button", { name: "Degraded health details" });
    expect(health.textContent).toContain("Degraded");

    fireEvent.click(health);
    expect(screen.getByText("heater · disconnected")).toBeTruthy();
    expect(screen.getByText("Calibration RTD is unavailable")).toBeTruthy();
  });

  it("lets the shaker report motor activity without masking degraded health", () => {
    render(<ShakerTile snapshot={degradedSnapshot} />);

    expect(screen.getByText("Busy")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Degraded health details" }),
    ).toBeTruthy();
  });

  it("uses the interactive health badge as the sole degraded status", () => {
    render(
      <TileShell
        snapshot={degradedSnapshot}
        displayStatus="degraded"
        headerRight={null}
      >
        <div>Tile body</div>
      </TileShell>,
    );

    expect(screen.getAllByText("Degraded")).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "Degraded health details" }),
    ).toBeTruthy();
  });
});
