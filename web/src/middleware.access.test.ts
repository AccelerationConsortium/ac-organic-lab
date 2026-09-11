// The owner-access probe (2026-09-10).
//
// `GET /api/equipment/{id}/control/access` asks an owner-gated device whether
// the signed-in account may control it (mt-xpr-balance-server). The control
// guard injected a verified identity for POST/PUT/PATCH/DELETE only, so the
// probe reached FastAPI anonymous and every device answered 401 — the XPR tiles
// read "Trusted dashboard authentication required" even for the configured
// owner. These tests pin the fix: the probe carries the verified identity, a
// signed-out probe stays anonymous (not refused), and no GET on a control path
// forwards a client-supplied identity.
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { NextRequest } from "next/server";

import { middleware } from "./middleware";

const AUTH_BASE = "http://127.0.0.1:8009";

function req(path: string, method = "GET", headers: Record<string, string> = {}) {
  return new NextRequest(new URL(`http://dash.test${path}`), {
    method,
    headers: new Headers({ cookie: "ac_auth_session=abc", ...headers }),
  });
}

function verifies(ok: boolean, user = "owner@utoronto.ca", role = "operator") {
  return vi.fn(async (url: string) => {
    if (!String(url).startsWith(`${AUTH_BASE}/auth/verify`)) throw new Error(`unexpected ${url}`);
    return {
      ok,
      headers: new Headers(ok ? { "x-auth-user": user, "x-auth-role": role } : {}),
    } as unknown as Response;
  });
}

beforeEach(() => {
  delete process.env.DASHBOARD_CONTROL_OPEN;
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GET /api/equipment/{id}/control/access", () => {
  it("carries the verified identity to the passthrough", async () => {
    const fetchMock = verifies(true, "owner@utoronto.ca", "operator");
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(req("/api/equipment/lle_xpr_balance/control/access"));
    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(res.headers.get("x-middleware-request-x-auth-user")).toBe("owner@utoronto.ca");
    expect(res.headers.get("x-middleware-request-x-auth-role")).toBe("operator");
  });

  it("stays anonymous — not refused — when signed out", async () => {
    vi.stubGlobal("fetch", verifies(false));
    const res = await middleware(req("/api/equipment/lle_xpr_balance/control/access"));
    // The device answers the anonymous probe itself (401), which the tile
    // renders as "Controls locked"; the middleware must not pre-empt that.
    expect(res.status).toBe(200);
    expect(res.headers.get("x-middleware-request-x-auth-user")).toBeNull();
  });

  it("replaces a forged identity with the verified one", async () => {
    vi.stubGlobal("fetch", verifies(true, "owner@utoronto.ca"));
    const res = await middleware(
      req("/api/equipment/gibbie_balance/control/access", "GET", {
        "x-auth-user": "someone.else@utoronto.ca",
        "x-auth-role": "admin",
      }),
    );
    expect(res.headers.get("x-middleware-request-x-auth-user")).toBe("owner@utoronto.ca");
    expect(res.headers.get("x-middleware-request-x-auth-role")).toBe("operator");
  });
});

describe("other GETs on a control path", () => {
  it("drop a client-supplied identity without consulting the sidecar", async () => {
    const fetchMock = verifies(true);
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(
      req("/api/equipment/xarm_translocation/control/graph", "GET", {
        "x-auth-user": "someone.else@utoronto.ca",
      }),
    );
    expect(res.status).toBe(200);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(res.headers.get("x-middleware-request-x-auth-user")).toBeNull();
    expect(res.headers.get("x-middleware-override-headers") ?? "").not.toMatch(/x-auth-user/);
  });
});

describe("writes on a control path", () => {
  it("are still refused when signed out", async () => {
    vi.stubGlobal("fetch", verifies(false));
    const res = await middleware(req("/api/equipment/lle_xpr_balance/control/weigh", "POST"));
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ detail: "Sign in to control equipment." });
  });
});
