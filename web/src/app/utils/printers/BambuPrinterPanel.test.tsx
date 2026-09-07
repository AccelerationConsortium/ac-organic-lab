// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
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
    expect(screen.getByText(/not attributable to anyone/)).toBeTruthy();
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
