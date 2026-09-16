// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { EquipmentSnapshot } from "@/types/api";
import { PrinterConnectionTile } from "./PrinterConnectionTile";

function snapshot(): EquipmentSnapshot {
  return {
    id: "elegoo_saturn_ultra_connection", name: "Elegoo Saturn Ultra", kind: "other",
    fetched_at: new Date().toISOString(), fetch_error: null,
    status: {
      equipment_status: "ready", activity: "unknown", allowed_actions: [],
      metrics: { printer_ip: {value: "192.0.2.18"}, ping: {value: 4.2, unit: "ms"} },
      components: {network: {connected: true, state: "up"}},
      message: "Network reachable.",
    },
  } as unknown as EquipmentSnapshot;
}
afterEach(cleanup);

describe("PrinterConnectionTile", () => {
  it("shows connection health without claiming print readiness or offering controls", () => {
    render(<PrinterConnectionTile snapshot={snapshot()} />);
    expect(screen.getByText("Connected")).toBeTruthy();
    expect(screen.getByText("192.0.2.18")).toBeTruthy();
    expect(screen.getByText("4.20 ms")).toBeTruthy();
    expect(screen.getByText(/Print progress and cloud connectivity are not monitored/)).toBeTruthy();
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("shows no reply as unreachable", () => {
    const s = snapshot(); s.status.equipment_status = "unknown";
    render(<PrinterConnectionTile snapshot={s} />);
    expect(screen.getByText("Unreachable")).toBeTruthy();
  });
  it("does not show stale success after a probe failure", () => {
    const s = snapshot(); s.fetch_error = {kind: "timeout", message: "probe timeout"};
    render(<PrinterConnectionTile snapshot={s} />);
    expect(screen.getByText("Unreachable")).toBeTruthy();
    expect(screen.queryByText("Connected")).toBeNull();
    expect(screen.queryByText("4.20 ms")).toBeNull();
  });
  it("shows sharing trouble separately from loss of printer reachability", () => {
    const s = snapshot(); s.status.equipment_status = "degraded";
    render(<PrinterConnectionTile snapshot={s} />);
    expect(screen.getByText("Connection degraded")).toBeTruthy();
  });
  it("allows a full polling interval before calling a reading stale", () => {
    const s = snapshot(); s.fetched_at = new Date(Date.now() - 60000).toISOString();
    render(<PrinterConnectionTile snapshot={s} />);
    expect(screen.queryByText(/stale ·/)).toBeNull();
  });
});
