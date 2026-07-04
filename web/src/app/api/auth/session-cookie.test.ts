// Tests for the ac_auth session-cookie scoping in the verify-code and logout
// proxy routes. AUTH_COOKIE_DOMAIN is read at module load, so each case
// resets the module registry and dynamically imports the route under test.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const DOMAIN = "tail6a1dd7.ts.net";
const SIDECAR_SET_COOKIE =
  "ac_auth_session=tok-abc123; HttpOnly; Max-Age=43200; Path=/; SameSite=lax; Secure";

const originalDomain = process.env.AUTH_COOKIE_DOMAIN;

function mockSidecar(response: {
  status: number;
  body?: unknown;
  setCookie?: string;
}) {
  const headers = new Headers({ "content-type": "application/json" });
  if (response.setCookie) headers.set("set-cookie", response.setCookie);
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(response.body ?? { ok: true }), {
      status: response.status,
      headers,
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function importRoute(route: "verify-code" | "logout", domain?: string) {
  vi.resetModules();
  if (domain === undefined) delete process.env.AUTH_COOKIE_DOMAIN;
  else process.env.AUTH_COOKIE_DOMAIN = domain;
  return route === "verify-code"
    ? import("./verify-code/route")
    : import("./logout/route");
}

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllGlobals();
  if (originalDomain === undefined) delete process.env.AUTH_COOKIE_DOMAIN;
  else process.env.AUTH_COOKIE_DOMAIN = originalDomain;
});

describe("POST /api/auth/verify-code", () => {
  const request = () =>
    new NextRequest("http://dashboard.local/api/auth/verify-code", {
      method: "POST",
      body: JSON.stringify({ email: "a@b.c", code: "123456" }),
      headers: { "content-type": "application/json" },
    });

  it("re-issues the session cookie with Domain when AUTH_COOKIE_DOMAIN is set", async () => {
    const { POST } = await importRoute("verify-code", DOMAIN);
    mockSidecar({ status: 200, setCookie: SIDECAR_SET_COOKIE });

    const res = await POST(request());
    expect(res.status).toBe(200);

    const setCookie = res.headers.get("set-cookie") ?? "";
    expect(setCookie).toContain("ac_auth_session=tok-abc123");
    expect(setCookie.toLowerCase()).toContain(`domain=${DOMAIN}`);
    expect(setCookie.toLowerCase()).toContain("httponly");
    expect(setCookie.toLowerCase()).toContain("samesite=lax");
    expect(setCookie.toLowerCase()).toContain("path=/");
    expect(setCookie).toContain("Max-Age=43200");
    // Re-issued on our plain-http origin, so never Secure. Since the
    // sidecar's raw cookie carries Secure, this also proves the raw header
    // was not relayed alongside the re-issued one.
    expect(setCookie.toLowerCase()).not.toContain("secure");
  });

  it("relays the sidecar Set-Cookie verbatim when AUTH_COOKIE_DOMAIN is unset", async () => {
    const { POST } = await importRoute("verify-code");
    mockSidecar({ status: 200, setCookie: SIDECAR_SET_COOKIE });

    const res = await POST(request());
    expect(res.status).toBe(200);
    expect(res.headers.get("set-cookie")).toBe(SIDECAR_SET_COOKIE);
  });

  it("sets no cookie on a sidecar rejection", async () => {
    const { POST } = await importRoute("verify-code", DOMAIN);
    mockSidecar({ status: 401, body: { detail: "Bad code." } });

    const res = await POST(request());
    expect(res.status).toBe(401);
    expect(res.headers.get("set-cookie")).toBeNull();
  });
});

describe("POST /api/auth/logout", () => {
  const request = () =>
    new NextRequest("http://dashboard.local/api/auth/logout", {
      method: "POST",
      headers: { cookie: "ac_auth_session=tok-abc123" },
    });

  it("clears the cookie with the same Domain when AUTH_COOKIE_DOMAIN is set", async () => {
    const { POST } = await importRoute("logout", DOMAIN);
    const fetchMock = mockSidecar({
      status: 200,
      setCookie: 'ac_auth_session=""; Max-Age=0; Path=/',
    });

    const res = await POST(request());
    expect(res.status).toBe(200);

    // The session cookie is forwarded so the sidecar revokes server-side.
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.cookie).toBe("ac_auth_session=tok-abc123");

    const setCookie = res.headers.get("set-cookie") ?? "";
    expect(setCookie).toContain("ac_auth_session=;");
    expect(setCookie.toLowerCase()).toContain(`domain=${DOMAIN}`);
    expect(setCookie).toContain("Max-Age=0");
  });

  it("still clears the domain cookie when the sidecar is unreachable", async () => {
    const { POST } = await importRoute("logout", DOMAIN);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("refused")));

    const res = await POST(request());
    expect(res.status).toBe(200);

    const setCookie = res.headers.get("set-cookie") ?? "";
    expect(setCookie).toContain("ac_auth_session=;");
    expect(setCookie.toLowerCase()).toContain(`domain=${DOMAIN}`);
    expect(setCookie).toContain("Max-Age=0");
  });

  it("relays the sidecar Set-Cookie verbatim when AUTH_COOKIE_DOMAIN is unset", async () => {
    const { POST } = await importRoute("logout");
    const clearing = 'ac_auth_session=""; Max-Age=0; Path=/';
    mockSidecar({ status: 200, setCookie: clearing });

    const res = await POST(request());
    expect(res.status).toBe(200);
    expect(res.headers.get("set-cookie")).toBe(clearing);
  });
});
