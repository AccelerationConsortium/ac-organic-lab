// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { BambuPrinterPanel } from "./BambuPrinterPanel";

// The panel reads the session to decide whether it is safe to frame the
// gateway page at all; see the auth cases below.
const auth = { loading: false, authenticated: true, requestLogin: vi.fn() };
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => auth }));

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

afterEach(() => {
  cleanup();
  auth.loading = false;
  auth.authenticated = true;
  auth.requestLogin = vi.fn();
});

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

  it("does not frame the page when nobody is signed in", () => {
    // The /bambu/* route 401s with the dashboard's own login HTML as its body,
    // which a browser renders — framing it unauthenticated showed the dashboard
    // nested inside itself.
    auth.authenticated = false;
    render(<BambuPrinterPanel printers={[]} />);

    expect(screen.queryByTitle("Bambu Gateway — submit a print")).toBeNull();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(screen.getByText(/same dashboard sign-in/)).toBeTruthy();
  });

  it("offers to start the login flow instead", () => {
    auth.authenticated = false;
    render(<BambuPrinterPanel printers={[]} />);

    screen.getByRole("button", { name: "Sign in" }).click();
    expect(auth.requestLogin).toHaveBeenCalledOnce();
  });

  it("frames nothing while the session is still being checked", () => {
    auth.loading = true;
    render(<BambuPrinterPanel printers={[]} />);

    expect(screen.queryByTitle("Bambu Gateway — submit a print")).toBeNull();
    expect(screen.getByText(/Checking your sign-in/)).toBeTruthy();
  });

  it("frames the submission page same-origin behind the edge", () => {
    render(<BambuPrinterPanel printers={[]} />);

    const frame = screen.getByTitle("Bambu Gateway — submit a print");
    // Same-origin path, not the gateway's own address: that is what puts it
    // behind the dashboard's login and lets the edge inject the identity.
    expect(frame.getAttribute("src")).toBe("/bambu/ui/?embed=1");
  });

  it("routes the standalone link through the authenticated edge", () => {
    render(<BambuPrinterPanel printers={[]} />);

    const link = screen.getByRole("link", { name: /Open submissions/ });
    expect(link.getAttribute("href")).toBe("/bambu/ui/");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noreferrer");
    expect(screen.queryByText(/Open directly/)).toBeNull();
  });

  it("explains attribution without suggesting an auth bypass", () => {
    render(<BambuPrinterPanel printers={[]} />);
    expect(screen.getByText(/signed-in account/)).toBeTruthy();
  });

  it("says that queueing a job does not reach a printer", () => {
    render(<BambuPrinterPanel printers={[]} />);
    expect(screen.getByText(/dispatch is not implemented/i)).toBeTruthy();
  });

  it("shows live progress, empty units and operator-declared transparent filament", () => {
    const printer = printerSnapshot("bambu_one", "Printer One", "P1S");
    printer.status.activity = "running";
    printer.status.metrics = { print_progress: {value: 35, unit: "%"}, remaining_time: {value: 20, unit: "min"} };
    printer.status.details = {job_name: "fixture.3mf", ams_unit_ids: [0, 128], ams_trays: [
      {ams_id: 128, tray_id: 0, tray_type: "PC", tray_color: "00000000", tray_color_name: "Transparent", tray_color_source: "operator_declared", remaining_percent: 12},
    ]};
    render(<BambuPrinterPanel printers={[printer]} />);
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("35");
    expect(screen.getByText("Transparent")).toBeTruthy();
    expect(screen.getByText("12%")).toBeTruthy();
    expect(screen.getByText(/Empty · no loaded filament/)).toBeTruthy();
  });

  it("withholds cached tray contents when telemetry is unavailable", () => {
    const printer = printerSnapshot("bambu_one", "Printer One", "P1S");
    printer.status.equipment_status = "unknown";
    printer.status.details = {ams_unit_ids: [0], ams_trays: [{ams_id: 0, tray_id: 0, tray_type: "PC", tray_color_name: "Transparent"}]};
    render(<BambuPrinterPanel printers={[printer]} />);
    expect(screen.queryByText("Transparent")).toBeNull();
    expect(screen.getByText("Telemetry unavailable")).toBeTruthy();
  });

  it("selects a printer using a same-origin message without reloading the form", () => {
    render(<BambuPrinterPanel printers={[printerSnapshot("bambu_one", "Printer One", "P1S")]} />);
    const frame = screen.getByTitle("Bambu Gateway — submit a print") as HTMLIFrameElement;
    const send = vi.spyOn(frame.contentWindow!, "postMessage");
    fireEvent.click(screen.getByRole("button", {name: "Prepare a job · view queue"}));
    expect(send).toHaveBeenCalledWith({type: "bambu:select-printer", printer: "bambu_one"}, window.location.origin);
    expect(frame.getAttribute("src")).toBe("/bambu/ui/?embed=1");
  });
});
