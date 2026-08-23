// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AdminPage from "./page";

// ---------------------------------------------------------------------------
// The admin console renders against the ac_auth sidecar's /admin/* proxies and
// the dashboard's /api/history/control-actions. These tests stub `fetch` per
// path and let the real react-query hooks run, so the page is exercised the
// way the browser would — including the headline tile's derived numbers.
// ---------------------------------------------------------------------------

const auth = {
  loading: false,
  authenticated: true,
  identity: { role: "admin", email: "yang@lab.ca" } as { role: string; email: string } | null,
};
vi.mock("@/lib/user-auth", () => ({
  useUserAuth: () => auth,
}));

const equipment = {
  data: {
    equipment: [
      {
        id: "plateloc",
        name: "Agilent PlateLoc",
        status: {
          details: {
            claimed_by: {
              session_id: "f1f1c1a2-0000-4000-8000-000000000000",
              owner: "agent:solubility",
              expires_at: "2026-08-23T15:00:00Z",
            },
          },
        },
      },
      { id: "shaker", name: "Shaker", status: { details: { claimed_by: null } } },
      { id: "env_hte", name: "Env HTE", status: { details: {} } },
    ],
  },
  error: null,
  isLoading: false,
};
vi.mock("@/lib/use-equipment", () => ({
  useEquipmentList: () => equipment,
}));

const NOW_S = Math.floor(Date.now() / 1000);

// Canned sidecar + history bodies, keyed by path prefix.
const bodies: Record<string, unknown> = {
  "/api/admin/state": {
    roster: { users: 3, automation: 1, projects: 2, active_accounts: 3 },
    roster_loaded_at: NOW_S - 600,
    last_reload: null,
    pending_automation: ["agent:pending@lab.ca"],
    expiring_soon: [{ email: "guest@lab.ca", expires_at: NOW_S + 5 * 86400 }],
  },
  "/api/admin/sessions": {
    sessions: [
      { email: "yang@lab.ca", created_at: NOW_S - 3600, expires_at: NOW_S + 8 * 3600 },
      { email: "yang@lab.ca", created_at: NOW_S - 7200, expires_at: NOW_S + 4 * 3600 },
    ],
    total_time_s: 25 * 3600,
  },
  "/api/admin/accounts": {
    users: [
      {
        email: "yang@lab.ca",
        name: "Yang Cao",
        role: "admin",
        status: "active",
        lab_account: "caoyang",
        notes: "",
        expires_at: null,
        is_expired: false,
        disabled_reason: "",
        grants: [],
        last_login_at: NOW_S - 3600,
        active_sessions: 2,
      },
      {
        email: "agent:solubility",
        name: "",
        role: "none",
        status: "active",
        lab_account: "",
        notes: "",
        expires_at: null,
        is_expired: false,
        disabled_reason: "",
        grants: [{ scope: "platform", id: "hte", role: "operator" }],
        last_login_at: null,
        active_sessions: 0,
      },
    ],
    automation: [
      {
        email: "agent:pending@lab.ca",
        name: "Pending bot",
        approved: false,
        platform: "hte",
        expires_at: null,
        is_expired: false,
        notes: "",
        api_keys: 1,
      },
    ],
  },
  "/api/admin/api-keys": {
    keys: [
      {
        id: 7,
        email: "agent:pending@lab.ca",
        label: "ci",
        created_at: NOW_S - 86400,
        expires_at: null,
        revoked: false,
        last_used_at: null,
      },
    ],
  },
  "/api/admin/auth-events": {
    events: [
      {
        ts: NOW_S - 3600,
        email: "yang@lab.ca",
        event: "login_success",
        detail: "",
        ip: "100.64.0.9",
        user_agent: "vitest",
      },
    ],
  },
  "/api/history/control-actions": {
    // The window is one row; the lifetime total is what the headline shows.
    actions: [
      {
        ts: "2026-08-21T19:32:06.217325+00:00",
        device_id: "xarm_translocation",
        message: null,
        action: "graph/move_to",
        method: "POST",
        status_code: 200,
        outcome: "ok",
        owner: "agent:jiaru",
        duration_s: 13.617,
        origin: "assistant",
      },
    ],
    total: 2758,
  },
};

function stubFetch() {
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
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AdminPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  stubFetch();
  auth.loading = false;
  auth.authenticated = true;
  auth.identity = { role: "admin", email: "yang@lab.ca" };
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AdminPage", () => {
  it("gates on an admin session client-side", async () => {
    auth.identity = { role: "operator", email: "op@lab.ca" };
    renderPage();
    expect(await screen.findByText(/sign in with an admin account/)).toBeTruthy();
    expect(screen.queryByText("Accounts & Activities")).toBeNull();
  });

  it("lays out eight half-width tiles in pairs, none spanning the grid", async () => {
    renderPage();
    await screen.findByText("Accounts & Activities");
    const titles = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(titles).toEqual([
      "Accounts & Activities",
      "Roster health",
      "Accounts",
      "Automation & API keys",
      "Live sessions",
      "Live claims",
      "Sign-in activity",
      "Control actions",
    ]);
    // Two-column grid; every tile is half-width (the headline is a
    // double-column tile: two columns of stats INSIDE a half-width card).
    expect(document.querySelector(".lg\\:grid-cols-2")).toBeTruthy();
    expect(document.querySelector("section.lg\\:col-span-2")).toBeNull();
  });

  it("combines the headline numbers into the Accounts & Activities tile", async () => {
    renderPage();
    const overview = (await screen.findByText("Accounts & Activities")).closest("section")!;
    // Lifetime total from the API, not the length of the fetched window.
    await within(overview).findByText("2,758");
    expect(within(overview).getByText("Active accounts")).toBeTruthy();
    expect(within(overview).getByText("Projects")).toBeTruthy();
    expect(within(overview).getByText("Live sessions")).toBeTruthy();
    expect(within(overview).getByText("Equipment claimed")).toBeTruthy();
    expect(within(overview).getByText("Control actions")).toBeTruthy();
    expect(within(overview).getByText("Session time")).toBeTruthy();
    expect(within(overview).getByText("Equipment")).toBeTruthy();
    expect(within(overview).getByText("Equipment claimed")).toBeTruthy();
    expect(within(overview).getByText("Total session time")).toBeTruthy();
    // Two live sessions, 1 h + 2 h signed in → 3 h; all-time 25 h stays in
    // hours (never days).
    await within(overview).findByText("3 h");
    expect(within(overview).getByText("25 h")).toBeTruthy();
    expect(within(overview).getByText("1 account signed in")).toBeTruthy();
    // Each column stacks its pair: Equipment sits directly above Equipment
    // claimed in the same column div.
    const equipmentCol = within(overview).getByText("Equipment").closest("div.flex")!;
    expect(equipmentCol.textContent).toContain("Equipment claimed");
  });

  it("folds roster alerts into the Roster health tile", async () => {
    renderPage();
    const health = (await screen.findByText("Roster health")).closest("section")!;
    await within(health).findByText("agent:pending@lab.ca");
    expect(within(health).getByText("guest@lab.ca")).toBeTruthy();
    expect(within(health).getByText("none since start")).toBeTruthy();
  });

  it("combines name and email in one Accounts cell and flags agents", async () => {
    renderPage();
    const accounts = (await screen.findByText("Accounts")).closest("section")!;
    const nameCell = (await within(accounts).findByText("Yang Cao")).closest("td")!;
    expect(nameCell.textContent).toContain("yang@lab.ca");
    expect(nameCell.textContent).toContain("caoyang");
    // A machine principal listed among users is marked, with its platform grant.
    const agentCell = within(accounts).getByText("agent:solubility").closest("td")!;
    expect(within(agentCell).getByText("agent")).toBeTruthy();
    expect(within(accounts).getByText("hte · platform")).toBeTruthy();
    // Only four columns survive the half-width tile.
    const heads = within(accounts)
      .getAllByRole("columnheader")
      .map((th) => th.textContent);
    expect(heads).toEqual(["Account", "Access", "Status", "Last login"]);
  });

  it("stacks device over action and outcome over duration in Control actions", async () => {
    renderPage();
    const tile = (await screen.findByText("Control actions", { selector: "h2" })).closest("section")!;
    const deviceCell = (
      await within(tile).findByText("xarm_translocation", { selector: "span" })
    ).closest("td")!;
    expect(deviceCell.textContent).toContain("POST graph/move_to");
    const outcomeCell = within(tile).getByText("ok (200)").closest("td")!;
    expect(outcomeCell.textContent).toContain("13.6 s");
    expect(within(tile).getByText("assistant")).toBeTruthy();
    expect(within(tile).getByText(/Latest 1 of 2,758/)).toBeTruthy();
  });
});
