// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EquipmentSnapshot } from "@/types/api";

import { LiquidHandlerTile } from "./LiquidHandlerTile";

const auth = { authenticated: true, canControl: () => true, requestLogin: vi.fn() };
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => auth }));

const postOt2Lights = vi.fn<(id: string, on: boolean) => Promise<{ ok: boolean }>>(async () => ({ ok: true }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  getDeckLayout: vi.fn(async () => ({ slots: {} })),
  postOt2Lights: (id: string, on: boolean) => postOt2Lights(id, on),
}));

function snap(lights: string): EquipmentSnapshot {
  return {
    id: "ot2_hte",
    name: "Opentrons OT-2 (HTE)",
    kind: "liquid_handler",
    fetched_at: "2026-09-09T00:00:00Z",
    latency_ms: 10,
    fetch_error: null,
    tile: { w: 2, h: 3 },
    status: {
      protocol_version: "1.2",
      equipment_id: "ot2_hte",
      equipment_name: "Opentrons OT-2 (HTE)",
      equipment_kind: "liquid_handler",
      equipment_status: "ready",
      activity: "idle",
      device_time: "2026-09-09T00:00:00Z",
      required_actions: [],
      allowed_actions: ["lights.set"],
      details: {},
      metrics: {},
      components: { lights: { connected: true, state: lights } },
    },
  } as unknown as EquipmentSnapshot;
}

function draw(snapshot: EquipmentSnapshot) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LiquidHandlerTile snapshot={snapshot} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  postOt2Lights.mockClear();
  auth.canControl = () => true;
  auth.authenticated = true;
});

describe("OT-2 HTTP status", () => {
  it.each([
    ["http", true, "connected"],
    ["http", false, "disconnected"],
    ["ssh", true, "disconnected"],
    [undefined, undefined, "unknown"],
  ] as const)("reports %s / %s as %s", (state, connected, expected) => {
    const snapshot = snap("off");
    snapshot.status.components = {
      ssh: { connected: false, state: "disconnected" },
      ...(state ? { control: { state, connected: connected! } } : {}),
    };
    draw(snapshot);
    const http = screen.getByTitle(`HTTP: ${expected}`);
    expect(screen.getByTitle("SSH: disconnected").nextElementSibling).toBe(http);
    expect(http.querySelector(".bg-emerald-400") !== null).toBe(expected === "connected");
  });
});

describe("OT-2 deck light", () => {
  it("sends the opposite of the reported state and shows it before the poll catches up", async () => {
    draw(snap("off"));
    const button = screen.getByRole("button", { name: /light/i });
    expect(button.getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(button);
    await waitFor(() => expect(postOt2Lights).toHaveBeenCalledWith("ot2_hte", true));
    // Optimistic: the pill flips now, not 2.5 s later when the poll returns.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /light/i }).getAttribute("aria-pressed")).toBe("true"),
    );
  });

  it("turns a lit deck off", async () => {
    draw(snap("on"));
    fireEvent.click(screen.getByRole("button", { name: /light/i }));
    await waitFor(() => expect(postOt2Lights).toHaveBeenCalledWith("ot2_hte", false));
  });

  it("stays a plain indicator without a role on this robot, since the passthrough would 403", () => {
    auth.canControl = () => false;
    draw(snap("on"));
    // Still legible as state — just not clickable.
    expect(screen.queryByRole("button", { name: /light/i })).toBeNull();
    expect(screen.getByTitle(/deck lights: on/i)).toBeTruthy();
  });
});
