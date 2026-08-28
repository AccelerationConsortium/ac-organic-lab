// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  EquipmentSnapshot,
  LabHostService,
  LabHostsResponse,
} from "@/types/api";

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

function service(overrides: Partial<LabHostService> & { id: string }): LabHostService {
  return {
    name: overrides.id,
    kind: "other",
    role: "service",
    base_url: "http://example:1234",
    host: "example",
    port: 1234,
    path: "/",
    adapter: "http",
    protocol: "1.0",
    enabled: true,
    ...overrides,
  };
}

// A trimmed /api/hosts payload — the shape api/app/hosts.py derives from
// equipment.yaml + the SSH whitelist.
const HOSTS: LabHostsResponse = {
  hosts: [
    {
      id: "gaia",
      label: "Central Server (gaia)",
      kind: "Linux server",
      hostname: "sdl2-server-gaia",
      services: [
        service({
          id: "kasa_tapo_gateway",
          name: "Camera & Plug Gateway",
          base_url: "http://127.0.0.1:8002",
          host: "127.0.0.1",
          port: 8002,
        }),
        // Edge path with no explicit port → labelled by path.
        service({
          id: "hermes_web",
          name: "Hermes Agent",
          base_url: "http://100.64.254.6/hermes/",
          host: "100.64.254.6",
          port: null,
          path: "/hermes/",
          adapter: "mock",
        }),
        service({
          id: "cam_hte_tapo_c245",
          name: "HTE Camera",
          kind: "camera",
          role: "equipment",
          base_url: "http://127.0.0.1:8002",
          host: "127.0.0.1",
          port: 8002,
        }),
      ],
    },
    {
      id: "cytation-pc",
      label: "Cytation PC",
      kind: "Windows PC",
      hostname: "sdl2-pc-03-cytation.tail6a1dd7.ts.net",
      services: [
        service({
          id: "plateloc",
          name: "Agilent PlateLoc",
          kind: "plate_sealer",
          role: "equipment",
          base_url: "http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8010",
          host: "sdl2-pc-03-cytation.tail6a1dd7.ts.net",
          port: 8010,
          protocol: "1.2",
        }),
        service({
          id: "hostops_cytation_pc",
          name: "Cytation PC",
          role: "ops",
          base_url: "http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8060",
          host: "sdl2-pc-03-cytation.tail6a1dd7.ts.net",
          port: 8060,
          protocol: "1.2",
        }),
      ],
    },
    {
      id: "uplc-pc",
      label: "UPLC PC",
      kind: "Windows PC",
      hostname: "sdl2-pc-06-uplc.tail6a1dd7.ts.net",
      services: [
        service({
          id: "hostops_uplc_pc",
          name: "UPLC PC",
          role: "ops",
          base_url: "http://sdl2-pc-06-uplc.tail6a1dd7.ts.net:8060",
          host: "sdl2-pc-06-uplc.tail6a1dd7.ts.net",
          port: 8060,
          protocol: "1.2",
        }),
      ],
    },
  ],
  other_hosts: [
    {
      hostname: "100.64.254.100",
      services: [
        service({
          id: "fume_hood_actuator",
          name: "Fume Hood Actuator",
          kind: "fume_hood",
          role: "equipment",
          base_url: "http://100.64.254.100:5000",
          host: "100.64.254.100",
          port: 5000,
        }),
      ],
    },
  ],
};

function opsSnapshot(
  id: string,
  state = "ready",
  details: Record<string, unknown> = {},
): EquipmentSnapshot {
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
      details,
    },
  } as unknown as EquipmentSnapshot;
}

const CYTATION_OPS_DETAILS = {
  backend: "nssm",
  services_whitelist: ["cytation", "plateloc", "xarm"],
  restartable: ["cytation", "plateloc"],
  probe_ports: [8010, 8040],
};

afterEach(() => {
  cleanup();
  auth.authenticated = true;
  auth.identity = { role: "admin", email: "admin@lab.ca" };
});

describe("HostsPanel", () => {
  it("renders config-derived service chips with ports, color-coded by role", () => {
    render(
      <HostsPanel
        hosts={HOSTS}
        snapshots={[opsSnapshot("hostops_cytation_pc", "ready", CYTATION_OPS_DETAILS)]}
      />,
    );

    const cytation = screen.getByText("Cytation PC").closest("article")!;
    expect(cytation.textContent).toContain("Windows PC");
    expect(cytation.textContent).toContain("sdl2-pc-03-cytation");
    // Equipment services are emerald, labelled name + registry port; the
    // tooltip carries the full base_url (the domain) from equipment.yaml.
    const plateloc = within(cytation).getByText("Agilent PlateLoc :8010");
    expect(plateloc.getAttribute("data-kind")).toBe("equipment");
    expect(plateloc.className).toContain("emerald");
    expect(plateloc.getAttribute("title")).toContain(
      "http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8010",
    );
    // Live hostops snapshot present → status badge.
    expect(cytation.textContent).toContain("Ready");

    // gaia: web services are sky; an edge path with no port labels by path.
    const gaia = screen.getByText("Central Server (gaia)").closest("article")!;
    const gateway = within(gaia).getByText("Camera & Plug Gateway :8002");
    expect(gateway.getAttribute("data-kind")).toBe("service");
    expect(gateway.className).toContain("sky");
    expect(within(gaia).getByText("Hermes Agent /hermes/")).toBeTruthy();
    // gaia runs no ops agent → no violet panel and no status badge slot.
    expect(gaia.textContent).not.toContain("Host ops");
  });

  it("renders the host-ops panel from the agent's live details", () => {
    render(
      <HostsPanel
        hosts={HOSTS}
        snapshots={[opsSnapshot("hostops_cytation_pc", "ready", CYTATION_OPS_DETAILS)]}
      />,
    );

    const cytation = screen.getByText("Cytation PC").closest("article")!;
    // Backend + port from config, whitelist from the live envelope, the
    // restartable subset marked ↻, and the loopback probe ports.
    expect(cytation.textContent).toContain("Host ops — sdl-lab-hostops · nssm");
    expect(cytation.textContent).toContain(":8060");
    const xarm = within(cytation).getByText("xarm");
    expect(xarm.textContent).not.toContain("↻");
    expect(within(cytation).getByText("plateloc").parentElement!.textContent).toContain("↻");
    expect(cytation.textContent).toContain(":8010 :8040");

    // Ops host without a snapshot yet degrades honestly.
    const uplc = screen.getByText("UPLC PC").closest("article")!;
    expect(uplc.textContent).toContain("no data");
    expect(uplc.textContent).toContain("No live details");
  });

  it("lists registry hosts outside the SSH whitelist under Other device hosts", () => {
    render(<HostsPanel hosts={HOSTS} snapshots={[]} />);

    expect(screen.getByText("Other device hosts")).toBeTruthy();
    const pi = screen.getByText("100.64.254.100").closest("article")!;
    const hood = within(pi).getByText("Fume Hood Actuator :5000");
    expect(hood.getAttribute("data-kind")).toBe("equipment");
    // No SSH link — the host is not on the console whitelist.
    expect(pi.querySelector("a[href^='/utils/computers/ssh/']")).toBeNull();
  });

  it("explains the chip colors in a legend", () => {
    render(<HostsPanel hosts={HOSTS} snapshots={[]} />);

    const legend = screen.getByRole("list", { name: "Capability color legend" });
    for (const label of ["web service", "lab-ops agent", "equipment service"]) {
      expect(within(legend).getByText(label)).toBeTruthy();
    }
  });

  it("shows unreachable when the hostops poll fails", () => {
    const down = {
      ...opsSnapshot("hostops_uplc_pc"),
      fetch_error: { kind: "timeout", message: "timed out", http_status: null },
    } as unknown as EquipmentSnapshot;
    render(<HostsPanel hosts={HOSTS} snapshots={[down]} />);

    const uplc = screen.getByText("UPLC PC").closest("article")!;
    expect(uplc.textContent).toContain("Unreachable");
  });

  it("links each host to its SSH terminal for an admin", () => {
    render(<HostsPanel hosts={HOSTS} snapshots={[]} />);

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
    render(<HostsPanel hosts={HOSTS} snapshots={[]} />);

    expect(document.querySelectorAll("a[href^='/utils/computers/ssh/']")).toHaveLength(0);
  });
});
