// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { HostsPanel } from "./HostsPanel";

// AuthGatedLink (which draws the SSH link) reads the session from here.
const auth = {
  authenticated: true,
  identity: { role: "admin", email: "admin@lab.ca" } as
    | { role: string; email: string }
    | null,
  canControl: () => true,
  requestLogin: () => {},
};
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => auth }));

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

afterEach(() => {
  cleanup();
  auth.authenticated = true;
  auth.identity = { role: "admin", email: "admin@lab.ca" };
});

describe("HostsPanel", () => {
  it("lists every host with OS and its capabilities as color-coded chips", () => {
    render(<HostsPanel snapshots={[opsSnapshot("hostops_cytation_pc")]} />);

    const cytation = screen.getByText("Cytation PC").closest("article")!;
    expect(cytation.textContent).toContain("Windows PC");
    expect(cytation.textContent).toContain("sdl2-pc-03-cytation");
    // One chip per capability, tinted by kind: service sky, equipment
    // emerald, the hostops agent violet.
    const plateloc = within(cytation).getByText("plateloc :8010");
    expect(plateloc.getAttribute("data-kind")).toBe("service");
    expect(plateloc.className).toContain("sky");
    const sealer = within(cytation).getByText("PlateLoc sealer");
    expect(sealer.getAttribute("data-kind")).toBe("equipment");
    expect(sealer.className).toContain("emerald");
    const ops = within(cytation).getByText("hostops :8060");
    expect(ops.getAttribute("data-kind")).toBe("ops");
    expect(ops.className).toContain("violet");
    // The prose the old fact rows held now rides the chip tooltip.
    expect(ops.getAttribute("title")).toContain("serial-port enumeration");
    // Live hostops snapshot present → status badge.
    expect(cytation.textContent).toContain("Ready");

    // Ops host without a snapshot yet degrades honestly; its USB portproxy
    // is a bridge-kind (amber) chip.
    const uplc = screen.getByText("UPLC PC").closest("article")!;
    expect(uplc.textContent).toContain("Windows PC");
    expect(uplc.textContent).toContain("no data");
    const bridge = within(uplc).getByText("OT-2 USB bridge :31950");
    expect(bridge.getAttribute("data-kind")).toBe("bridge");
    expect(bridge.className).toContain("amber");

    // gaia runs no ops agent → no violet chip and no status badge slot.
    const gaia = screen.getByText("Central Server (gaia)").closest("article")!;
    expect(gaia.textContent).toContain("Linux server");
    expect(gaia.querySelector("[data-kind='ops']")).toBeNull();
  });

  it("explains the chip colors in a legend", () => {
    render(<HostsPanel snapshots={[]} />);

    const legend = screen.getByRole("list", { name: "Capability color legend" });
    for (const label of ["service", "lab-ops agent", "controls equipment", "network bridge"]) {
      expect(within(legend).getByText(label)).toBeTruthy();
    }
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

  it("links each host to its SSH terminal for an admin", () => {
    render(<HostsPanel snapshots={[]} />);

    const cytation = screen.getByText("Cytation PC").closest("article")!;
    const link = cytation.querySelector("a[href='/utils/computers/ssh/cytation-pc']");
    expect(link).not.toBeNull();
    // New tab: the terminal is a working surface, not a navigation step.
    expect(link!.getAttribute("target")).toBe("_blank");

    expect(
      screen.getByText("Central Server (gaia)").closest("article")!
        .querySelector("a[href='/utils/computers/ssh/gaia']"),
    ).not.toBeNull();
  });

  it("shows no SSH link to a non-admin", () => {
    auth.identity = { role: "operator", email: "op@lab.ca" };
    render(<HostsPanel snapshots={[]} />);

    expect(document.querySelectorAll("a[href^='/utils/computers/ssh/']")).toHaveLength(0);
  });
});