import { afterEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "./route";

afterEach(() => { vi.unstubAllGlobals(); });
const request = (cookie = "ac_auth_session=fixture-session") => new NextRequest("http://dashboard.test/equipment/lle_hplc/control/frame", { headers: { cookie, "x-api-key": "forged", "x-auth-user": "forged", "x-auth-role": "admin" } });

it("refuses missing cookies even with forged identity and API-key headers", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  const response = await GET(request(""));
  expect(response.status).toBe(401);
  expect(fetch).not.toHaveBeenCalled();
  expect(await response.text()).not.toContain("DEMO-014");
  expect(response.headers.get("cache-control")).toContain("no-store");
});

it("verifies only the session cookie and returns a private, isolated document", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(null, { headers: { "x-auth-user": "fixture-user" } })); vi.stubGlobal("fetch", fetch);
  const response = await GET(request());
  expect(response.status).toBe(200);
  const [url, init] = fetch.mock.calls[0];
  expect(url).toMatch(/\/auth\/verify$/);
  expect(init.headers).toEqual({ cookie: "ac_auth_session=fixture-session" });
  expect(init.cache).toBe("no-store");
  expect(init.redirect).toBe("error");
  expect(response.headers.get("content-type")).toContain("text/html");
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  expect(response.headers.get("content-security-policy")).toContain("connect-src 'none'");
  expect(response.headers.get("content-security-policy")).toContain("sandbox allow-scripts");
  expect(response.headers.get("x-frame-options")).toBe("SAMEORIGIN");
  const document = await response.text();
  expect(document).toContain("Design preview");
  expect(document).toContain("simulated data");
});

it.each([401, 403, 500])("fails closed on auth response %s", async status => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status })));
  const response = await GET(request());
  expect(response.status).toBe(status === 500 ? 503 : 401);
  expect(await response.text()).not.toContain("DEMO-014");
});

it("fails closed when verification lacks an identity", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null)));
  expect((await GET(request())).status).toBe(503);
});

it("fails closed if the auth service is unavailable", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  expect((await GET(request())).status).toBe(503);
});
