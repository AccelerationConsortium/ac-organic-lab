// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
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