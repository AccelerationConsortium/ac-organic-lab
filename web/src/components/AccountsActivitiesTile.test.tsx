// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountsActivitiesTile } from "./AccountsActivitiesTile";

const auth = {
  loading: false,
  authenticated: true,
  identity: { role: "admin", email: "yang@lab.ca" } as { role: string } | null,
};
vi.mock("@/lib/user-auth", () => ({ useUserAuth: () => auth }));
vi.mock("@/lib/use-equipment", () => ({
  useEquipmentList: () => ({
    data: {
      equipment: [
        { id: "a", name: "A", status: { details: { claimed_by: { owner: "x" } } } },
        { id: "b", name: "B", status: { details: {} } },
      ],
    },
  }),
}));

const bodies: Record<string, unknown> = {
  "/api/overview/state": {
    roster: { users: 2, automation: 1, projects: 3, active_accounts: 2 },
  },
  "/api/overview/sessions": {
    live: { count: 0, accounts: 0, seconds: 0 },
    total_time_s: 7200,
  },
  "/api/history/control-actions": { actions: [], total: 42 },
};

function renderTile(props: { wide?: boolean; adminLink?: boolean } = {}) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AccountsActivitiesTile {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  auth.authenticated = true;
  auth.identity = { role: "admin" };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const key = Object.keys(bodies).find((k) => url.startsWith(k));
      if (!key) return new Response("not found", { status: 404 });
      return new Response(JSON.stringify(bodies[key]), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AccountsActivitiesTile", () => {
  it("renders nothing (and fetches nothing) for a signed-out viewer", () => {
    auth.authenticated = false;
    auth.identity = null;
    const { container } = renderTile();
    expect(container.innerHTML).toBe("");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("renders for a non-admin signed-in viewer, with no GO link", async () => {
    auth.identity = { role: "operator" };
    renderTile();
    await screen.findByText("42"); // lifetime total, not window length
    expect(screen.getByText("Accounts & Activities")).toBeTruthy();
    expect(screen.getByText("Equipment claimed").closest("div.flex")!.textContent).toContain("2");
    // aggregate data loads for any signed-in role...
    expect(screen.getByText("Active accounts").closest("div")!.textContent).toContain("2");
    expect(screen.getByText("Live sessions").closest("div")!.textContent).toContain("0");
    // ...but the admin console link stays admin-only
    expect(screen.queryByRole("link", { name: "GO →" })).toBeNull();
  });

  it("shows the headline pairs, with a GO link when asked", async () => {
    renderTile({ adminLink: true });
    await screen.findByText("42"); // lifetime total, not window length
    expect(screen.getByText("Accounts & Activities")).toBeTruthy();
    expect(screen.getByText("Equipment claimed").closest("div.flex")!.textContent).toContain("2");
    // all-time signed-in figure from the sidecar (7200 s → "2 h")
    expect(screen.getByText("Total session").closest("div")!.textContent).toContain("2 h");
    const go = screen.getByRole("link", { name: "GO →" });
    expect(go.getAttribute("href")).toBe("/admin");
  });

  it("shows an em dash for total session time until the sidecar serves it", async () => {
    bodies["/api/overview/sessions"] = {
      live: { count: 0, accounts: 0, seconds: 0 },
    };
    renderTile();
    await screen.findByText("42");
    expect(screen.getByText("Total session").closest("div")!.textContent).toContain("—");
    bodies["/api/overview/sessions"] = {
      live: { count: 0, accounts: 0, seconds: 0 },
      total_time_s: 7200,
    };
  });

  it("omits the GO link on the admin console itself", async () => {
    renderTile();
    await screen.findByText("42");
    expect(screen.queryByRole("link", { name: "GO →" })).toBeNull();
  });
});
