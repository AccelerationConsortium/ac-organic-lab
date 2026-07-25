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
  it("shows the activity label with an amber health glyph for a degraded-but-running device", () => {
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

    // Two separate elements: activity carries the text, health the glyph.
    expect(screen.getByText("Running")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Health: Degraded" })).toBeTruthy();
    expect(screen.queryByText("Degraded")).toBeNull();
  });

  it("shows only the activity label when health is nominal", () => {
    const ready = {
      ...gatewaySnapshot,
      pill: {},
      activity: "idle",
      activity_source: "status",
    } as unknown as EquipmentSnapshot;

    render(<PlatformCard id="web_services" title="Services" snapshots={[ready]} />);

    expect(screen.getByText("Idle")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("renders unknown activity as a dash, never a false Idle", () => {
    const legacy = {
      ...gatewaySnapshot,
      pill: {},
      // v1.0/v1.1 device in a state the invariants can't pin: no activity
      status: { ...gatewaySnapshot.status, equipment_status: "dry_run" },
    } as unknown as EquipmentSnapshot;

    render(<PlatformCard id="web_services" title="Services" snapshots={[legacy]} />);

    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.queryByText("Idle")).toBeNull();
  });
});
