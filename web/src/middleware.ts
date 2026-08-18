import { NextRequest, NextResponse } from "next/server";

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

// `device` covers the root-level, claim-exempt device actions proxied by
// api/app/control.py's /device/* route (xArm connect/disconnect/stop/clear) —
// gated exactly like /control/* so those writes always require a signed-in user.
// `deck` covers the shared OT-2 deck-layout store (api/app/deck.py): the PUT
// that rewrites the layout is a write, so it requires a signed-in user here and
// a per-equipment role check in the backend (only admin / authorized users of
// that device may change its deck). The GET is a public read (not a write
// method), so it is never gated.
const CONTROL_PATH_RE = /^\/api\/equipment\/[^/]+\/(?:control|sash|device|deck)(?:\/.*)?$/;
const CONTROL_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// The central labware store: reads are public, but saving/deleting shared
// definitions is a write (admin-enforced in the FastAPI handler via the
// X-Auth-Role we inject here after verifying the session).
const LABWARE_PATH_RE = /^\/api\/labware(?:\/.*)?$/;

// -- /api/assistant/* gate (Phase 2) -----------------------------------------
//
// The lab assistant is read-only for hardware but can read ALL lab history,
// so every method (including GET) requires a signed-in user — except the
// /health liveness probe, which exposes config only. The verified identity is
// injected as X-Auth-User so the assistant backend records who asked (the
// backend Claude account is shared; attribution lives here). The same
// DASHBOARD_CONTROL_OPEN escape hatch opens it for local dev.

const ASSISTANT_PATH_RE = /^\/api\/assistant(?:\/.*)?$/;
const ASSISTANT_PUBLIC_PATHS = new Set(["/api/assistant/health"]);

// -- /admin page + /api/admin/* proxy gate (admin role required) --------------
//
// The admin page enumerates the allow-list, sign-in history, and the control
// audit trail, so both the page navigation and its XHR proxy require a valid
// session whose role resolves to `admin` (from the sidecar's X-Auth-Role).
// Page navigations by non-admins are redirected to the Overview; XHR gets a
// JSON 401/403. The sidecar independently enforces admin on every /admin/*
// endpoint (defense in depth — this gate is UX, that one is authority).
// DASHBOARD_CONTROL_OPEN opens it for local dev without the sidecar.

const ADMIN_PAGE_RE = /^\/admin(?:\/.*)?$/;
const ADMIN_API_RE = /^\/api\/admin(?:\/.*)?$/;

// Stale Notebooks bookmark. /notebooks no longer exists as an in-dashboard
// route (Bitácora opens in its own browser tab). An old bookmark (or a direct
// URL) is redirected to Bitácora for a signed-in visitor, or back to the
// dashboard Overview when they aren't — they may not have Bitácora access, and
// the Overview is where they'd sign in. (/inventory is a real public page
// again — a chrome-less embed — so it is not redirected.)
const STALE_ELN_REDIRECT_RE = /^\/notebooks(?:\/.*)?$/;

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

  // ---- Stale /notebooks → Bitácora (or Overview) --------------------------
  if (STALE_ELN_REDIRECT_RE.test(pathname)) {
    let to = "/";
    if (CONTROL_OPEN || (await verifySession(request)).ok) to = "/bitacora/";
    const url = request.nextUrl.clone();
    url.pathname = to;
    url.search = "";
    return NextResponse.redirect(url);
  }

  // ---- Assistant guard (it reads all lab history — sign-in required) -----
  if (
    ASSISTANT_PATH_RE.test(pathname) &&
    !ASSISTANT_PUBLIC_PATHS.has(pathname)
  ) {
    const headers = new Headers(request.headers);
    headers.delete("x-auth-user");
    headers.delete("x-auth-role");

    if (!CONTROL_OPEN) {
      const v = await verifySession(request);
      if (!v.ok) {
        return NextResponse.json(
          { detail: "Sign in to use the lab assistant." },
          { status: 401 },
        );
      }
      if (v.user) headers.set("x-auth-user", v.user);
      if (v.role) headers.set("x-auth-role", v.role);
    }

    return NextResponse.next({ request: { headers } });
  }

  // ---- Admin page / proxy guard (admin role required) --------------------
  if (ADMIN_PAGE_RE.test(pathname) || ADMIN_API_RE.test(pathname)) {
    if (CONTROL_OPEN) return NextResponse.next();
    const v = await verifySession(request);
    if (!v.ok || v.role !== "admin") {
      if (ADMIN_API_RE.test(pathname)) {
        return NextResponse.json(
          { detail: v.ok ? "Admin only." : "Sign in as an admin." },
          { status: v.ok ? 403 : 401 },
        );
      }
      const url = request.nextUrl.clone();
      url.pathname = "/";
      return NextResponse.redirect(url);
    }
    return NextResponse.next();
  }

  // ---- Control-surface guard (view-only until signed in) ----------------
  if (
    CONTROL_METHODS.has(request.method) &&
    (CONTROL_PATH_RE.test(pathname) || LABWARE_PATH_RE.test(pathname))
  ) {
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
  matcher: [
    "/api/equipment/:path*",
    "/api/assistant/:path*",
    "/api/labware/:path*",
    "/admin/:path*",
    "/api/admin/:path*",
    "/notebooks/:path*",
  ],
};
