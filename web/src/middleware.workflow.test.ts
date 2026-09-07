// The workflow-run gate (2026-09-07 safety review, finding 1).
//
// `/api/workflow/*` was absent from the matcher below, so middleware never ran
// for it: a request reached the Next rewrite, was proxied to FastAPI, and the
// backend read whatever `X-Auth-User` the client sent. These tests pin both
// halves of the fix — the matcher selects the path at all, and the handler
// strips the client's identity and injects only a verified one.
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { NextRequest } from "next/server";

import { config, middleware } from "./middleware";

const AUTH_BASE = "http://127.0.0.1:8009";

function req(path: string, method = "POST", headers: Record<string, string> = {}) {
  return new NextRequest(new URL(`http://dash.test${path}`), {
    method,
    headers: new Headers({ cookie: "ac_auth_session=abc", ...headers }),
  });
}

// The sidecar answers with the resolved principal in RESPONSE HEADERS, not a
// body — see `verifySession` in middleware.ts.
function verifies(ok: boolean, user = "op@utoronto.ca", role = "none") {
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

describe("the /api/workflow matcher", () => {
  it("selects the workflow routes at all", () => {
    // The bug was exactly this: no entry, so none of the logic below ever ran.
    expect(config.matcher).toContain("/api/workflow/:path*");
  });
});

describe("starting and aborting a run", () => {
  it("refuses an unauthenticated start", async () => {
    vi.stubGlobal("fetch", verifies(false));
    const res = await middleware(req("/api/workflow/runs"));
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ detail: "Sign in to start or abort a run." });
  });

  it("refuses an unauthenticated abort", async () => {
    vi.stubGlobal("fetch", verifies(false));
    expect((await middleware(req("/api/workflow/runs/run_1/abort"))).status).toBe(401);
  });

  it("fails closed when the auth sidecar is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    expect((await middleware(req("/api/workflow/runs"))).status).toBe(401);
  });

  it("injects the verified identity and drops the client's own", async () => {
    vi.stubGlobal("fetch", verifies(true, "real@utoronto.ca", "admin"));
    const res = await middleware(
      req("/api/workflow/runs", "POST", {
        // The forgery the backend used to believe.
        "x-auth-user": "someone.else@utoronto.ca",
        "x-auth-role": "admin",
      }),
    );
    expect(res.status).toBe(200);          // NextResponse.next()
    const forwarded = res.headers.get("x-middleware-override-headers") ?? "";
    expect(forwarded).toContain("x-auth-user");
    expect(res.headers.get("x-middleware-request-x-auth-user")).toBe("real@utoronto.ca");
    expect(res.headers.get("x-middleware-request-x-auth-role")).toBe("admin");
  });

  it("strips a client identity from a READ too, and does not gate it", async () => {
    // Reads are ungated like every other read here — but the backend forwards
    // X-Auth-User to bitácora when it fetches the authorization, so a
    // client-chosen value must not survive on any method.
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const res = await middleware(
      req("/api/workflow/runs/run_1/events", "GET", { "x-auth-user": "forged@utoronto.ca" }),
    );
    expect(res.status).toBe(200);
    expect(fetchSpy).not.toHaveBeenCalled();      // no session check on a read
    expect(res.headers.get("x-middleware-request-x-auth-user")).toBeNull();
  });
});
