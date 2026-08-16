// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { BambuPrinterPanel } from "./BambuPrinterPanel";

vi.mock("@/lib/use-control-lock", () => ({
  useControlLock: () => ({
    locked: true,
    countdown: 0,
    toggle: vi.fn(),
  }),
}));

function printerSnapshot(id: string, name: string, model: string): EquipmentSnapshot {
  return {
    id,
    name,
    kind: "other",
    fetched_at: "2026-07-22T12:00:00Z",
    latency_ms: 5,
    status: {
      protocol_version: "1.0",
      equipment_id: id,
      equipment_name: name,
      equipment_kind: "other",
      equipment_status: "ready",
      device_time: "2026-07-22T12:00:00Z",
      message: "Printer is idle",
      required_actions: [],
      allowed_actions: [],
      components: {
        mqtt: { connected: true, state: "ready" },
      },
      metrics: {
        bed_temperature: { value: 25, unit: "C" },
      },
      details: { device_type: "3d_printer", model, monitoring_only: true },
    },
  } as unknown as EquipmentSnapshot;
}

afterEach(cleanup);

describe("BambuPrinterPanel", () => {
  it("shows both monitored printers and the read-only boundary", () => {
    render(
      <BambuPrinterPanel
        printers={[
          printerSnapshot("bambu_p1s_01", "Bambu P1S 01", "P1S"),
          printerSnapshot("bambu_h2d_01", "Bambu H2D 01", "H2D"),
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "Bambu Printers" })).toBeTruthy();
    expect(screen.getByText("Bambu P1S 01")).toBeTruthy();
    expect(screen.getByText("Bambu H2D 01")).toBeTruthy();
    expect(screen.getByText(/Monitoring only/)).toBeTruthy();
  });
});
