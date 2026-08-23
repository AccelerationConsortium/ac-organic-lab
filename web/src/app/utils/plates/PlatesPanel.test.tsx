// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/user-auth", () => ({
  useUserAuth: () => ({ authenticated: true, canControl: true, requestLogin: () => {} }),
}));

const api = vi.hoisted(() => ({
  getCustodyPlates: vi.fn(),
  getCustodyPlate: vi.fn(),
  getLocations: vi.fn(),
  postCustodyMove: vi.fn(),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api")>();
  return { ...mod, ...api };
});

import { PlatesPanel } from "./PlatesPanel";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PlatesPanel />
    </QueryClientProvider>,
  );
}

describe("PlatesPanel", () => {
  it("groups plates by place and offers only registry places in the move form", async () => {
    api.getCustodyPlates.mockResolvedValue({
      plates: [
        { hid: "PLT-2", container_id: "c2", container_type: "plate", model: "corning_96", status: "in_use",
          location_id: "l2", location: "torry_pines_shaker/nest", equipment_id: "torry_pines_shaker", project_id: null },
        { hid: "PLT-1", container_id: "c1", container_type: "plate", model: "corning_96", status: "empty",
          location_id: null, location: null, equipment_id: null, project_id: null },
      ],
    });
    api.getLocations.mockResolvedValue({
      locations: [
        { name: "bench/hte_staging", type: "storage", equipment: null, capacity: 10, label: "HTE bench", active: true, aliases: {}, notes: null },
        { name: "retired/place", type: "storage", equipment: null, capacity: 1, label: null, active: false, aliases: {}, notes: null },
      ],
    });
    renderPanel();
    await waitFor(() => expect(screen.getByText("PLT-2")).toBeTruthy());
    expect(screen.getByText("torry_pines_shaker/nest")).toBeTruthy();
    expect(screen.getByText("— never placed —")).toBeTruthy();
    const select = screen.getByLabelText("Destination place") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toContain("bench/hte_staging");
    expect(options).not.toContain("retired/place");
  });

  it("shows an unreachable ledger as unreachable, not as an empty lab", async () => {
    api.getCustodyPlates.mockRejectedValue(new Error("record layer unreachable"));
    api.getLocations.mockResolvedValue({ locations: [] });
    renderPanel();
    await waitFor(() => expect(screen.getByText(/Could not read the custody ledger/)).toBeTruthy());
    expect(screen.queryByText(/No plates are registered/)).toBeNull();
  });
});
