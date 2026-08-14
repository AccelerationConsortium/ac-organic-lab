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

const requiresInitSnapshot = {
  ...degradedSnapshot,
  status: {
    ...degradedSnapshot.status,
    equipment_status: "requires_init",
    required_actions: ["startup"],
    components: {},
  },
} as unknown as EquipmentSnapshot;

describe("TileShell lifecycle INIT affordance", () => {
  it("renders the off-state toggle as an INIT button when initLabel is set", () => {
    const onPowerToggle = vi.fn();
    render(
      <TileShell
        snapshot={requiresInitSnapshot}
        headerRight={null}
        lifecycle={{ isOn: false, initLabel: "INIT", onPowerToggle }}
      >
        <div>Tile body</div>
      </TileShell>,
    );

    const init = screen.getByRole("button", { name: /INIT/ });
    // Primary (call-to-action) emphasis, not the muted OFF chip.
    expect(init.className).toContain("emerald");
    fireEvent.click(init);
    expect(onPowerToggle).toHaveBeenCalledTimes(1);
  });

  it("keeps the muted OFF chip when no initLabel is given (literal power)", () => {
    render(
      <TileShell
        snapshot={requiresInitSnapshot}
        headerRight={null}
        lifecycle={{ isOn: false, onPowerToggle: vi.fn() }}
      >
        <div>Tile body</div>
      </TileShell>,
    );

    const off = screen.getByRole("button", { name: /OFF/ });
    expect(off.className).not.toContain("emerald");
  });

  it("shows ON while running even when initLabel is set", () => {
    render(
      <TileShell
        snapshot={degradedSnapshot}
        headerRight={null}
        lifecycle={{ isOn: true, initLabel: "INIT", onPowerToggle: vi.fn() }}
      >
        <div>Tile body</div>
      </TileShell>,
    );

    expect(screen.getByRole("button", { name: /ON/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /INIT/ })).toBeNull();
  });

  it("surfaces INIT on a requires_init shaker tile end-to-end", () => {
    render(<ShakerTile snapshot={requiresInitSnapshot} />);
    expect(screen.getByRole("button", { name: /INIT/ })).toBeTruthy();
  });
});
