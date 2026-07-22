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
