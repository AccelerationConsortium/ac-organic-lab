// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { HostsPanel } from "./HostsPanel";

function opsSnapshot(id: string, state: string): EquipmentSnapshot {
  return {
    id,
    name: id,
    kind: "other",
    fetched_at: "2026-08-16T12:00:00Z",
    latency_ms: 5,
    status: {
      protocol_version: "1.2",
      equipment_id: id,
      equipment_name: id,
      equipment_kind: "other",
      equipment_status: state,
      activity: "idle",
      device_time: "2026-08-16T12:00:00Z",
      required_actions: [],
      allowed_actions: [],
      components: {},
      metrics: {},
      details: {},
    },
  } as unknown as EquipmentSnapshot;
}

afterEach(cleanup);

describe("HostsPanel", () => {
  it("shows live host-ops status for ops hosts and marks the rest", () => {
    render(<HostsPanel snapshots={[opsSnapshot("hostops_cytation_pc", "ready")]} />);

    // Both groups render.
    expect(screen.getByText("Servers")).toBeTruthy();
    expect(screen.getByText("Device PCs")).toBeTruthy();

    // Ops host with a snapshot: badge + live state label.
    const cytation = screen.getByText("Cytation PC").closest("li")!;
    expect(cytation.textContent).toContain("host-ops");
    expect(cytation.textContent).toContain("Ready");

    // Ops host whose snapshot is missing degrades honestly.
    const uplc = screen.getByText("UPLC PC").closest("li")!;
    expect(uplc.textContent).toContain("host-ops: no data");

    // Non-ops host is labelled as such, with no badge.
    const gaia = screen.getByText("Central Server (gaia)").closest("li")!;
    expect(gaia.textContent).toContain("no ops agent");
    expect(gaia.textContent).not.toContain("host-ops:");
  });
});
