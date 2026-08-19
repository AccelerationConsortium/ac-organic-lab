// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { EquipmentStatusCard } from "./EquipmentStatusCard";

vi.mock("@/lib/use-control-lock", () => ({
  useControlLock: () => ({
    locked: false,
    noAccess: false,
    countdown: 0,
    unlock: vi.fn(),
    lock: vi.fn(),
    toggle: vi.fn(),
  }),
}));

const postGenericStartup = vi.fn(async (_equipmentId: string) => ({ ok: true }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  postGenericStartup: (equipmentId: string) => postGenericStartup(equipmentId),
}));

function snapshot(over: {
  equipment_status: string;
  allowed_actions?: string[];
  required_actions?: string[];
  fetch_error?: object | null;
}): EquipmentSnapshot {
  return {
    id: "dose_every_well",
    name: "Dose Every Well",
    kind: "solid_doser",
    fetched_at: "2026-08-15T00:00:00Z",
    latency_ms: 10,
    fetch_error: over.fetch_error ?? null,
    status: {
      protocol_version: "1.1",
      equipment_id: "dose_every_well",
      equipment_name: "Dose Every Well",
      equipment_kind: "solid_doser",
      equipment_status: over.equipment_status,
      device_time: "2026-08-15T00:00:00Z",
      required_actions: over.required_actions ?? [],
      allowed_actions: over.allowed_actions ?? [],
      details: {},
      metrics: {},
      components: {},
    },
  } as unknown as EquipmentSnapshot;
}

afterEach(() => {
  cleanup();
  postGenericStartup.mockClear();
});

describe("EquipmentStatusCard generic INIT", () => {
  it("offers INIT on a requires_init device that advertises startup, and posts it", () => {
    render(
      <EquipmentStatusCard
        snapshot={snapshot({
          equipment_status: "requires_init",
          allowed_actions: ["startup"],
          required_actions: ["startup"],
        })}
      />,
    );
    const init = screen.getByRole("button", { name: /init/i });
    fireEvent.click(init);
    expect(postGenericStartup).toHaveBeenCalledWith("dose_every_well");
  });

  it("stays button-free when the device does not advertise startup", () => {
    // The device is the authority (STATUS_SPEC §6.2): the fume hood's init
    // is a sash.move, not a startup — the generic card must not invent one.
    render(
      <EquipmentStatusCard
        snapshot={snapshot({
          equipment_status: "requires_init",
          required_actions: ["sash.move"],
          allowed_actions: ["sash.move"],
        })}
      />,
    );
    expect(screen.queryByRole("button", { name: /init/i })).toBeNull();
  });

  it("does not offer INIT on an unreachable device", () => {
    render(
      <EquipmentStatusCard
        snapshot={snapshot({
          equipment_status: "requires_init",
          allowed_actions: ["startup"],
          fetch_error: { kind: "timeout", message: "unreachable" },
        })}
      />,
    );
    expect(screen.queryByRole("button", { name: /init/i })).toBeNull();
  });

  it("does not offer INIT on a ready device", () => {
    render(
      <EquipmentStatusCard
        snapshot={snapshot({ equipment_status: "ready", allowed_actions: ["startup", "shutdown"] })}
      />,
    );
    expect(screen.queryByRole("button", { name: /init/i })).toBeNull();
  });
});
