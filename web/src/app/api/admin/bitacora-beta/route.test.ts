import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "./route";

const tester = "tester@example.test";
const request = (headers: Record<string, string> = { cookie: "ac_auth_session=opaque" }) =>
  new NextRequest("http://localhost/api/admin/bitacora-beta", { headers });

beforeEach(() => { vi.stubEnv("BITACORA_BETA_TESTER_EMAIL", tester); });
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe("private beta gate", () => {
  it("is disabled without a configured tester", async () => {
    vi.stubEnv("BITACORA_BETA_TESTER_EMAIL", "");
    expect((await GET(request())).status).toBe(404);
  });
  it("rejects forged identity headers and API keys without a cookie", async () => {
    expect((await GET(request({ "x-auth-user": tester, "x-auth-role": "admin", "x-api-key": "fake" }))).status).toBe(401);
  });
  it.each([["someone@example.test", "admin"], [tester, "operator"], ["", "admin"]])(
    "rejects verified identity %s with role %s", async (user, role) => {
      vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { headers: { "x-auth-user": user, "x-auth-role": role } })));
      expect((await GET(request())).status).toBe(403);
    },
  );
  it("accepts only the tester and forwards solely verified headers", async () => {
    const fetcher = vi.fn(async (_url: string, _init: RequestInit) => new Response(null, { headers: {
      "x-auth-user": tester, "x-auth-role": "admin", "x-auth-projects": "verified-project",
    } }));
    vi.stubGlobal("fetch", fetcher);
    const response = await GET(request({ cookie: "ac_auth_session=opaque", "x-api-key": "never-forward", "x-auth-pi-projects": "forged" }));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ href: "/bitacora-beta", label: "Bitácora Beta", user: tester });
    expect(response.headers.get("x-auth-projects")).toBe("verified-project");
    expect(response.headers.get("x-auth-pi-projects")).toBe("");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(fetcher.mock.calls[0][1].headers).toEqual({ cookie: "ac_auth_session=opaque" });
  });
  it("fails closed during an auth outage or expired session", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    expect((await GET(request())).status).toBe(503);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 401 })));
    expect((await GET(request())).status).toBe(401);
  });
});
