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

  it("frames the submission page same-origin behind the edge", () => {
    render(<BambuPrinterPanel printers={[]} />);

    const frame = screen.getByTitle("Bambu Gateway — submit a print");
    // Same-origin path, not the gateway's own address: that is what puts it
    // behind the dashboard's login and lets the edge inject the identity.
    expect(frame.getAttribute("src")).toBe("/bambu/ui/");
  });

  it("keeps a direct link as a fallback, and says it is not attributable", () => {
    render(<BambuPrinterPanel printers={[]} />);

    const link = screen.getByRole("link", { name: /Open directly/ });
    // Absolute tailnet URL: the registry reaches this gateway on loopback,
    // which in a browser is the visitor's own machine.
    expect(link.getAttribute("href")).toBe("http://100.64.254.6:8012/ui");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noreferrer");
    expect(screen.getByText(/not attributable/)).toBeTruthy();
  });

  it("explains a blank panel rather than leaving it mysterious", () => {
    render(<BambuPrinterPanel printers={[]} />);
    expect(screen.getByText(/edge route is not installed yet/)).toBeTruthy();
  });

  it("says that queueing a job does not reach a printer", () => {
    render(<BambuPrinterPanel printers={[]} />);
    expect(screen.getByText(/dispatch is not implemented/i)).toBeTruthy();
  });
});
