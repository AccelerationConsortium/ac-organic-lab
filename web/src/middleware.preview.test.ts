import { afterEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { middleware, config } from "./middleware";

const path = "/preview/dashboard/overview";
function request(headers: Record<string, string> = {}, suffix = "") {
  return new NextRequest(`http://dashboard.test${path}${suffix}`, { headers });
}
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it.each([undefined, "false", "1"])("returns 404 with flag %s, before authentication", async value => {
  vi.stubEnv("DASHBOARD_SIDEBAR_PREVIEW", value);
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  expect((await middleware(request())).status).toBe(404);
  expect(fetch).not.toHaveBeenCalled();
});

it("matches the entire preview tree", () => {
  expect(config.matcher).toContain("/preview/dashboard/:path*");
});

it.each<Record<string, string>>([{}, { "x-api-key": "machine", "x-auth-role": "admin", "x-auth-user": "forged" }])("rejects missing browser cookie despite forged identity/API key", async headers => {
  vi.stubEnv("DASHBOARD_SIDEBAR_PREVIEW", "true");
  vi.stubEnv("DASHBOARD_CONTROL_OPEN", "true");
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  expect((await middleware(request(headers))).status).toBe(401);
  expect(fetch).not.toHaveBeenCalled();
});

it.each([
  [true, "admin", "human", 200], [true, "operator", "human", 403],
  [true, "admin", "", 401], [false, "admin", "human", 401],
] as const)("verifies browser identity (%s, %s, %s)", async (ok, role, user, status) => {
  vi.stubEnv("DASHBOARD_SIDEBAR_PREVIEW", "true");
  const fetch = vi.fn().mockResolvedValue({ ok, headers: new Headers({ "x-auth-role": role, "x-auth-user": user }) });
  vi.stubGlobal("fetch", fetch);
  const result = await middleware(request({ cookie: "ac_auth_session=real; other=ignored", "x-api-key": "forged", RSC: "1", "next-router-prefetch": "1" }, "?_rsc=test"));
  expect(result.status).toBe(status);
  expect(result.headers.get("cache-control")).toBe("private, no-store");
  expect(fetch.mock.calls[0][1]).toMatchObject({ headers: { cookie: "ac_auth_session=real" }, cache: "no-store" });
  expect(fetch.mock.calls[0][1].headers["x-api-key"]).toBeUndefined();
});

it("fails closed on auth outage", async () => {
  vi.stubEnv("DASHBOARD_SIDEBAR_PREVIEW", "true");
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  expect((await middleware(request({ cookie: "ac_auth_session=real" }))).status).toBe(401);
});

it("does not change the production root", async () => {
  vi.stubEnv("DASHBOARD_SIDEBAR_PREVIEW", "true");
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  expect((await middleware(new NextRequest("http://dashboard.test/"))).status).toBe(200);
  expect(fetch).not.toHaveBeenCalled();
});
