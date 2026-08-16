// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { HostsPanel } from "./HostsPanel";

function opsSnapshot(id: string, state = "ready"): EquipmentSnapshot {
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
  it("lists every host with OS, services, controlled equipment and ops API", () => {
    render(<HostsPanel snapshots={[opsSnapshot("hostops_cytation_pc")]} />);

    const cytation = screen.getByText("Cytation PC").closest("article")!;
    expect(cytation.textContent).toContain("Windows PC");
    expect(cytation.textContent).toContain("sdl2-pc-03-cytation");
    expect(cytation.textContent).toContain("plateloc :8010");
    expect(cytation.textContent).toContain("PlateLoc sealer");
    expect(cytation.textContent).toContain("serial-port enumeration");
    // Live hostops snapshot present → status badge.
    expect(cytation.textContent).toContain("Ready");

    // Ops host without a snapshot yet degrades honestly.
    const uplc = screen.getByText("UPLC PC").closest("article")!;
    expect(uplc.textContent).toContain("Windows PC");
    expect(uplc.textContent).toContain("no data");
    expect(uplc.textContent).toContain("UPLC-MS sidecar");

    // gaia has no ops agent and says so.
    const gaia = screen.getByText("Central Server (gaia)").closest("article")!;
    expect(gaia.textContent).toContain("Linux server");
    expect(gaia.textContent).toContain("no ops agent");
  });

  it("shows unreachable when the hostops poll fails", () => {
    const down = {
      ...opsSnapshot("hostops_uplc_pc"),
      fetch_error: { kind: "timeout", message: "timed out", http_status: null },
    } as unknown as EquipmentSnapshot;
    render(<HostsPanel snapshots={[down]} />);

    const uplc = screen.getByText("UPLC PC").closest("article")!;
    expect(uplc.textContent).toContain("Unreachable");
  });
});
