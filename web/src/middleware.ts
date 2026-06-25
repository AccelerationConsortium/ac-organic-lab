import { NextRequest, NextResponse } from "next/server";

// -- /api-reference page gate (pre-existing) --------------------------------

const API_REF_COOKIE = "api_ref_auth";
const API_REF_PREFIX = "/api-reference";
const API_REF_UNLOCK = "/api-reference/unlock";

// -- /api/equipment/*/{control,sash}/* gate (view-only until signed in) -----
//
// The dashboard is view-only until a user signs in. Every POST/PUT/PATCH/
// DELETE on the control passthrough must carry a valid ac_auth session (the
// `ac_auth_session` cookie) — or an X-Api-Key for machine principals — which
// we validate against the sidecar's GET /auth/verify. On success we inject
// the verified X-Auth-User / X-Auth-Role into the forwarded request so the
// FastAPI passthrough (control.py) audits the real operator; on failure we
// reject with 401 (JSON, since these are XHR endpoints).
//
// Every control is gated — including cameras, env sensors and the OT-2 deck
// lights (there is no convenience bypass). Reads (GET) are never gated.
//
// Escape hatch: set DASHBOARD_CONTROL_OPEN=true to disable the gate entirely
// (local dev without the auth sidecar running). Default is closed.

const CONTROL_PATH_RE = /^\/api\/equipment\/[^/]+\/(?:control|sash)(?:\/.*)?$/;
const CONTROL_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

const AUTH_SERVICE_BASE =
  process.env.AUTH_SERVICE_BASE ?? "http://127.0.0.1:8009";
const CONTROL_OPEN = process.env.DASHBOARD_CONTROL_OPEN === "true";

// Validate the caller's session (cookie) or machine principal (X-Api-Key)
// against the auth sidecar. Returns the resolved identity on success.
// Fails closed (ok: false) when the sidecar is unreachable.
async function verifySession(
  request: NextRequest,
): Promise<{ ok: boolean; user?: string; role?: string }> {
  try {
    const res = await fetch(`${AUTH_SERVICE_BASE}/auth/verify`, {
      headers: {
        cookie: request.headers.get("cookie") ?? "",
        "x-api-key": request.headers.get("x-api-key") ?? "",
      },
      cache: "no-store",
    });
    if (!res.ok) return { ok: false };
    return {
      ok: true,
      user: res.headers.get("x-auth-user") ?? undefined,
      role: res.headers.get("x-auth-role") ?? undefined,
    };
  } catch {
    return { ok: false };
  }
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // ---- /api-reference page guard ----------------------------------------
  if (
    pathname.startsWith(API_REF_PREFIX) &&
    !pathname.startsWith(API_REF_UNLOCK)
  ) {
    const password = process.env.API_REF_PASSWORD;
    if (password) {
      const cookie = request.cookies.get(API_REF_COOKIE)?.value;
      if (cookie !== password) {
        const url = request.nextUrl.clone();
        url.pathname = API_REF_UNLOCK;
        url.searchParams.set("next", pathname);
        return NextResponse.redirect(url);
      }
    }
    return NextResponse.next();
  }

  // ---- Control-surface guard (view-only until signed in) ----------------
  if (CONTROL_METHODS.has(request.method) && CONTROL_PATH_RE.test(pathname)) {
    // Never trust a client-supplied identity header; we set it only after
    // verifying a session, so control.py's audit owner can't be forged.
    const headers = new Headers(request.headers);
    headers.delete("x-auth-user");
    headers.delete("x-auth-role");

    if (!CONTROL_OPEN) {
      const v = await verifySession(request);
      if (!v.ok) {
        return NextResponse.json(
          { detail: "Sign in to control equipment." },
          { status: 401 },
        );
      }
      if (v.user) headers.set("x-auth-user", v.user);
      if (v.role) headers.set("x-auth-role", v.role);
    }

    return NextResponse.next({ request: { headers } });
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/api-reference/:path*", "/api/equipment/:path*"],
};
