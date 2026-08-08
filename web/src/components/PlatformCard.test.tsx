// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { PlatformCard } from "./PlatformCard";

vi.mock("@/lib/user-auth", () => ({
  useUserAuth: () => ({
    authenticated: false,
    canControl: () => false,
    requestLogin: vi.fn(),
  }),
}));

const gatewaySnapshot = {
  id: "bambu_gateway",
  name: "Bambu Printers",
  kind: "other",
  fetched_at: "2026-07-22T12:00:00Z",
  latency_ms: 5,
  base_url: "http://127.0.0.1:8012",
  pill: {
    link_label: "GO",
    link_href: "/utils/bambu_printer",
    internal: true,
  },
  status: {
    protocol_version: "1.0",
    equipment_id: "bambu_gateway",
    equipment_name: "Bambu Gateway",
    equipment_kind: "other",
    equipment_status: "ready",
    device_time: "2026-07-22T12:00:00Z",
    required_actions: [],
    allowed_actions: [],
    components: {},
    metrics: {},
    details: { monitoring_only: true, printer_count: 2 },
  },
} as unknown as EquipmentSnapshot;

const externalSnapshot = {
  ...gatewaySnapshot,
  id: "xarm_translocation",
  name: "UFactory xArm5",
  pill: {
    link_label: "Open",
    link_href: "http://100.64.254.6/xarm5/web/",
    internal: false,
  },
} as unknown as EquipmentSnapshot;

afterEach(cleanup);

describe("PlatformCard service links", () => {
  it("renders an internal monitoring page link without control authorization", () => {
    render(
      <PlatformCard
        id="web_services"
        title="Services"
        snapshots={[gatewaySnapshot]}
      />,
    );

    expect(screen.getByText("Bambu Printers")).toBeTruthy();
    const link = screen.getByRole("link", { name: "GO →" });
    expect(link.getAttribute("href")).toBe("/utils/bambu_printer");
    expect(link.getAttribute("target")).toBeNull();
    expect(link.getAttribute("aria-disabled")).toBeNull();
  });

  it("renders external interfaces as Open with a northeast arrow", () => {
    render(
      <PlatformCard
        id="web_services"
        title="Services"
        snapshots={[externalSnapshot]}
      />,
    );

    const link = screen.getByRole("link", { name: "Open ↗" });
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("renders same-tab platform navigation as GO with a right arrow", () => {
    render(
      <PlatformCard
        id="hte"
        title="HTE Platform"
        href="/platforms/hte"
        snapshots={[]}
      />,
    );

    const link = screen.getByRole("link", { name: "GO →" });
    expect(link.getAttribute("target")).toBeNull();
  });
});

describe("PlatformCard equipment row health + activity (spec v1.2)", () => {
  it("shows two independent dots for a degraded-but-running device", () => {
    const shaker = {
      ...gatewaySnapshot,
      id: "torry_pines_shaker",
      name: "Torrey Pines SC25XR",
      kind: "shaker",
      pill: {},
      activity: "running",
      activity_source: "components",
      status: {
        ...gatewaySnapshot.status,
        equipment_id: "torry_pines_shaker",
        equipment_kind: "shaker",
        equipment_status: "degraded",
      },
    } as unknown as EquipmentSnapshot;

    render(<PlatformCard id="hte" title="HTE" snapshots={[shaker]} />);

    // Two separate dots: neither axis hides the other, and neither renders
    // its state as visible text — the labels live in the tooltips.
    expect(screen.getByRole("img", { name: "Health: Degraded" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Activity: Running" })).toBeTruthy();
    // The state names live only in the hover bubbles, never as row text.
    expect(screen.queryByText("Degraded")).toBeNull();
    expect(screen.queryByText("Running")).toBeNull();
  });

  it("shows a nominal health dot alongside the activity dot", () => {
    const ready = {
      ...gatewaySnapshot,
      pill: {},
      activity: "idle",
      activity_source: "status",
    } as unknown as EquipmentSnapshot;

    render(<PlatformCard id="web_services" title="Services" snapshots={[ready]} />);

    expect(screen.getByRole("img", { name: "Health: Ready" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Activity: Idle" })).toBeTruthy();
  });

  it("names unknown activity in the tooltip, never a false Idle", () => {
    const legacy = {
      ...gatewaySnapshot,
      pill: {},
      // v1.0/v1.1 device in a state the invariants can't pin: no activity
      status: { ...gatewaySnapshot.status, equipment_status: "dry_run" },
    } as unknown as EquipmentSnapshot;

    render(<PlatformCard id="web_services" title="Services" snapshots={[legacy]} />);

    expect(screen.getByRole("img", { name: "Activity: Unknown" })).toBeTruthy();
    expect(screen.queryByRole("img", { name: "Activity: Idle" })).toBeNull();
  });
  it("renders unreachable (fetch_error) as the two warning-bubble dots", () => {
    // The whole-PC-off case: transport failure → effectiveState "unreachable"
    // on the health dot, activity honestly Unknown on the activity dot —
    // never a stale state, never a fake idle.
    const down = {
      ...gatewaySnapshot,
      id: "plateloc",
      name: "Agilent PlateLoc",
      kind: "plate_sealer",
      pill: {},
      activity: "unknown",
      activity_source: "none",
      fetch_error: { kind: "timeout", message: "timed out", http_status: null },
      status: {
        ...gatewaySnapshot.status,
        equipment_status: "unknown", // synthetic envelope
      },
    } as unknown as EquipmentSnapshot;

    render(<PlatformCard id="hte" title="HTE" snapshots={[down]} />);

    expect(screen.getByRole("img", { name: "Health: Unreachable" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Activity: Unknown" })).toBeTruthy();
  });
});

describe("PlatformCard camera preview selection", () => {
  const cameraSnapshot = (id: string, name: string) =>
    ({
      ...gatewaySnapshot,
      id,
      name,
      kind: "camera",
      pill: {},
      status: {
        ...gatewaySnapshot.status,
        equipment_id: id,
        equipment_name: name,
        equipment_kind: "camera",
        details: { lenses: [], streaming_enabled: true, privacy_mode: false },
      },
    }) as unknown as EquipmentSnapshot;

  // A section may hold more than one camera (complexation has the platform C245 plus
  // the OT-2-facing C100). Only the FIRST one gets the preview region, so a
  // second camera is opted out of Overview streaming purely by ordering it
  // later in platforms.yaml — no per-camera flag exists. Ordering is therefore
  // load-bearing config; this pins it.
  it("previews only the first camera when a section has several", () => {
    render(
      <PlatformCard
        id="complexation"
        title="Echem Platform"
        snapshots={[
          cameraSnapshot("cam_echem_tapo_c245", "Echem Platform Camera"),
          cameraSnapshot("cam_echem_tapo_c100", "Echem OT2 Camera"),
        ]}
      />,
    );

    // Exactly one stream toggle and one preview region, and that region
    // names the first camera — the second appears as an equipment row only.
    expect(screen.getAllByTitle("Show camera stream")).toHaveLength(1);
    const preview = screen.getByText("Stream hidden").parentElement!;
    expect(preview.textContent).toContain("Echem Platform Camera");
    expect(preview.textContent).not.toContain("Echem OT2 Camera");
  });
});
