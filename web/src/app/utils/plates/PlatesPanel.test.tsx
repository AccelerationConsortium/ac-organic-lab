// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CustodyMap, CustodyNode } from "@/lib/api";

vi.mock("@/lib/user-auth", () => ({
  useUserAuth: () => ({ authenticated: true, canControl: true, requestLogin: () => {} }),
}));

const api = vi.hoisted(() => ({
  getCustodyMap: vi.fn(),
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

function node(over: Partial<CustodyNode> & { hid: string; container_id: string }): CustodyNode {
  return {
    container_type: "plate", model: null, status: "empty", location_id: null, location: null,
    equipment_id: null, project_id: null, seat: null, seat_conflict: false, sites: null,
    occupants: [], chain_masked: false, ...over,
  };
}

const vial = (hid: string, id: string, site: string, conflict = false) =>
  node({ hid, container_id: id, container_type: "vial", status: "in_use", location_id: "l3",
         location: "ot2_complexation/slot_3", seat: { container_id: "r1", hid: "RK-003", site, readable: true },
         seat_conflict: conflict });

const MAP: CustodyMap = {
  sections: [
    {
      id: "complexation", title: "Complexation Platform",
      places: [
        { name: "ot2_complexation/slot_3", label: "slot 3", type: "deck", equipment_id: "ot2_complexation", capacity: 1, registered: true,
          containers: [node({ hid: "RK-003", container_id: "r1", container_type: "rack", status: "in_use", location_id: "l3",
                              location: "ot2_complexation/slot_3", sites: ["A1", "A2", "B3"],
                              occupants: [vial("V-0107", "v1", "B3", true), vial("V-0108", "v2", "B3", true)] })] },
        { name: "ot2_complexation/slot_4", label: "slot 4", type: "deck", equipment_id: "ot2_complexation", capacity: 1, registered: true, containers: [] },
      ],
    },
    {
      id: "other", title: "Benches, storage and waste",
      places: [
        { name: "bench/hte_staging", label: "HTE bench", type: "storage", equipment_id: null, capacity: 10, registered: true,
          containers: [node({ hid: "PLT-2", container_id: "p2", status: "in_use", location_id: "lb", location: "bench/hte_staging" })] },
      ],
    },
  ],
  unplaced: [node({ hid: "PLT-1", container_id: "p1" })],
  counts: { containers: 5, placed: 4 },
};

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PlatesPanel />
    </QueryClientProvider>,
  );
}

describe("PlatesPanel (lab map)", () => {
  it("renders places by platform, a rack as a grid of sites, and flags a double-booked seat", async () => {
    api.getCustodyMap.mockResolvedValue(MAP);
    api.getLocations.mockResolvedValue({
      locations: [
        { name: "bench/hte_staging", type: "storage", equipment: null, capacity: 10, label: "HTE bench", active: true, aliases: {}, notes: null },
        { name: "retired/place", type: "storage", equipment: null, capacity: 1, label: null, active: false, aliases: {}, notes: null },
      ],
    });
    renderPanel();
    await waitFor(() => expect(screen.getByText("Complexation Platform")).toBeTruthy());
    expect(screen.getByText("Benches, storage and waste")).toBeTruthy();
    // the rack at slot 3 shows its three declared sites; B3 holds both vials and is flagged
    const rack = screen.getByTestId("adapter-RK-003");
    expect(within(rack).getByTestId("site-RK-003-A1")).toBeTruthy();
    const b3 = within(rack).getByTestId("site-RK-003-B3");
    expect(within(b3).getByLabelText("V-0107 (seat conflict)")).toBeTruthy();
    expect(within(b3).getByLabelText("V-0108 (seat conflict)")).toBeTruthy();
    expect(b3.title).toContain("two containers recorded here");
    expect(within(rack).getByText("2/3 sites")).toBeTruthy();
    // a loose plate at the bench; a never-placed one in its own list; empty places hidden by default
    expect(screen.getByText("PLT-2")).toBeTruthy();
    expect(screen.getByText("Never placed")).toBeTruthy();
    expect(screen.getByText("PLT-1")).toBeTruthy();
    expect(screen.queryByTestId("place-ot2_complexation/slot_4")).toBeNull();
    fireEvent.click(screen.getByLabelText("show empty places"));
    expect(screen.getByTestId("place-ot2_complexation/slot_4")).toBeTruthy();
    // the move form offers only active registry places
    const select = screen.getByLabelText("Destination place") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toContain("bench/hte_staging");
    expect(options).not.toContain("retired/place");
  });

  it("finds a vial by hid through its rack and a place by name", async () => {
    api.getCustodyMap.mockResolvedValue(MAP);
    api.getLocations.mockResolvedValue({ locations: [] });
    renderPanel();
    await waitFor(() => expect(screen.getByText("PLT-2")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Find a container or place"), { target: { value: "v-0108" } });
    expect(screen.getByTestId("adapter-RK-003")).toBeTruthy();
    expect(screen.queryByText("PLT-2")).toBeNull();
    expect(screen.queryByText("Never placed")).toBeNull();
    fireEvent.change(screen.getByLabelText("Find a container or place"), { target: { value: "bench/" } });
    expect(screen.getByText("PLT-2")).toBeTruthy();
    expect(screen.queryByTestId("adapter-RK-003")).toBeNull();
    fireEvent.change(screen.getByLabelText("Find a container or place"), { target: { value: "zzz" } });
    expect(screen.getByText(/Nothing matches/)).toBeTruthy();
  });

  it("shows an unreachable ledger as unreachable, not as an empty lab", async () => {
    api.getCustodyMap.mockRejectedValue(new Error("record layer unreachable"));
    api.getLocations.mockResolvedValue({ locations: [] });
    renderPanel();
    await waitFor(() => expect(screen.getByText(/Could not read the custody ledger/)).toBeTruthy());
    expect(screen.queryByText(/No containers are registered/)).toBeNull();
  });

  it("labels a carrier's move in a vial's history and shows where it is seated", async () => {
    api.getCustodyMap.mockResolvedValue(MAP);
    api.getLocations.mockResolvedValue({ locations: [] });
    api.getCustodyPlate.mockResolvedValue({
      ...vial("V-0107", "v1", "B3"),
      history: [
        { action_id: "a1", action_type: "receive", to_location_id: null, to_container_id: "r1", to_site: "B3",
          source_container_id: null, target_container_id: "v1", performed_by: "bench", performed_at: "2026-10-09T10:00:00Z",
          step_id: null, plan_id: null, params: {} },
        { action_id: "a2", action_type: "move", to_location_id: "l3", source_container_id: null, target_container_id: "r1",
          performed_by: "xarm_translocation", performed_at: "2026-10-09T11:00:00Z", step_id: null, plan_id: null, params: {} },
      ],
    });
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("adapter-RK-003")).toBeTruthy());
    fireEvent.click(screen.getAllByLabelText("V-0107 (seat conflict)")[0]);
    await waitFor(() => expect(screen.getByText(/moved with its carrier: move/)).toBeTruthy());
    expect(screen.getByText(/receive → seat B3 of/)).toBeTruthy();
    expect(screen.getByText(/Seated at B3 of/)).toBeTruthy();
  });

  it("records a seat picked from the rack grid, and warns about an occupied site instead of refusing it", async () => {
    api.getCustodyMap.mockResolvedValue(MAP);
    api.getLocations.mockResolvedValue({ locations: [] });
    api.postCustodyMove.mockResolvedValue({ recorded: true, hid: "V-0109", to: "RK-003 @ A1", seat: { adapter_hid: "RK-003", site: "A1" } });
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("adapter-RK-003")).toBeTruthy());
    // the form starts in place mode; an empty site in the grid is a button that switches it to that seat
    expect(screen.getByLabelText("Destination place")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Seat a container at RK-003 A1"));
    const carrier = screen.getByLabelText("Destination carrier") as HTMLSelectElement;
    const site = screen.getByLabelText("Destination site") as HTMLSelectElement;
    expect(carrier.value).toBe("RK-003");
    expect(site.value).toBe("A1");
    expect(screen.queryByLabelText("Destination place")).toBeNull();
    // only the carriers the map shows are offered, with the manifest's sites
    expect(Array.from(carrier.options).map((o) => o.value)).toEqual(["", "RK-003"]);
    expect(Array.from(site.options).map((o) => o.value)).toEqual(["", "A1", "A2", "B3"]);
    // an occupied site warns (D2: recorded and flagged, never refused) — the button stays enabled
    fireEvent.change(site, { target: { value: "B3" } });
    expect(screen.getByText(/B3 already holds V-0107, V-0108/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Plate hid"), { target: { value: "V-0109" } });
    expect((screen.getByText("Record move") as HTMLButtonElement).disabled).toBe(false);
    // a container cannot be seated in itself
    fireEvent.change(screen.getByLabelText("Plate hid"), { target: { value: "RK-003" } });
    expect(screen.getByText(/cannot be seated in itself/)).toBeTruthy();
    expect((screen.getByText("Record move") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Plate hid"), { target: { value: "V-0109" } });
    fireEvent.change(site, { target: { value: "A1" } });
    fireEvent.click(screen.getByText("Record move"));
    await waitFor(() =>
      expect(api.postCustodyMove).toHaveBeenCalledWith({ hid: "V-0109", seat: { adapter_hid: "RK-003", site: "A1" }, note: undefined }),
    );
    await waitFor(() => expect(screen.getByText("RK-003 @ A1")).toBeTruthy());
  });
});
