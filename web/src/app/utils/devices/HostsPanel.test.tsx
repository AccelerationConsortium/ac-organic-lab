// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { HostsPanel } from "./HostsPanel";

vi.mock("@/lib/use-control-lock", () => ({
  useControlLock: () => ({
    locked: true,
    countdown: 0,
    toggle: vi.fn(),
  }),
}));

function opsSnapshot(id: string, name: string): EquipmentSnapshot {
  return {
    id,
    name,
    kind: "other",
    fetched_at: "2026-08-16T12:00:00Z",
    latency_ms: 5,
    status: {
      protocol_version: "1.2",
      equipment_id: id,
      equipment_name: name,
      equipment_kind: "other",
      equipment_status: "ready",
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
  it("renders ops hosts as live equipment tiles and the rest as static cards", () => {
    render(
      <HostsPanel
        snapshots={[opsSnapshot("hostops_cytation_pc", "Cytation PC Ops")]}
      />,
    );

    // Ops host with a snapshot: the real equipment tile (name + status pill).
    const cytation = screen.getByText("Cytation PC Ops").closest("article")!;
    expect(cytation.textContent).toContain("hostops_cytation_pc");
    expect(cytation.textContent).toContain("Ready");

    // Ops host whose snapshot is missing degrades to a static card.
    const uplc = screen.getByText("UPLC PC Ops").closest("article")!;
    expect(uplc.textContent).toContain("host-ops agent — no data yet");

    // Non-ops host: static card, marked as such, with its services listed.
    const gaia = screen.getByText("Central Server (gaia)").closest("article")!;
    expect(gaia.textContent).toContain("no ops agent");
    expect(gaia.textContent).toContain("sdl2-server-gaia");
  });
});
