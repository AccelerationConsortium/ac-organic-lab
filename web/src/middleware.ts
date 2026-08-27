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

// Plate custody (docs/PLATE_TRACKING.md D5): reads are public, but recording
// a bench-top move is a write to the lab's custody ledger, attributed to the
// human — so it needs a signed-in user, whose verified identity we inject as
// X-Auth-User for api/app/custody.py to record as the mover.
const CUSTODY_PATH_RE = /^\/api\/custody(?:\/.*)?$/;

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

// -- SSH console gate (admin HUMANS only — never a machine principal) --------
//
// A shell on a device PC sits BELOW every safety layer the lab has (claims,
// allowed_actions, the four interlock layers, the propose-only assistant), so
// this surface is deliberately the narrowest one in the dashboard:
//
//   * role must resolve to `admin`, and
//   * the session must be a real browser session — we verify with the COOKIE
//     ONLY and never forward X-Api-Key, so an API-key principal cannot mint a
//     ticket even if its roster role is admin. docs/AGENTIC_LAB_DESIGN.md
//     Part II keeps the unattended `lab-runner` agent free of any terminal
//     toolset for exactly this reason (it ingests Slack); this is the web-side
//     half of that rule.
//
// /api/ssh/ws is EXEMPT, like /xarm5/ws and /hermes/api/ws at the edge. The
// socket instead presents a single-use, 30 s ticket minted by
// POST /api/ssh/session — which DOES pass through this gate. See
// api/app/ssh_console.py.
//
// The exemption is load-bearing in the `matcher` below, NOT in the early
// return here, and the difference is not cosmetic: Next resolves routes for
// an upgrade request with the raw socket standing in for the response, and
// invoking middleware in that state throws inside the server
// ("Error handling upgrade request TypeError: Cannot read properties of
// undefined (reading 'bind')") and kills the handshake before the rewrite to
// FastAPI is ever reached. Measured against next 14.2.35. So the matcher must
// never select the ws path; the early return below is only a guard in case
// someone widens it again.
const SSH_API_RE = /^\/api\/ssh(?:\/.*)?$/;
const SSH_WS_PATH = "/api/ssh/ws";
const SSH_PAGE_RE = /^\/utils\/computers\/ssh(?:\/.*)?$/;

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
  opts: { cookieOnly?: boolean } = {},
): Promise<{ ok: boolean; user?: string; role?: string }> {
  try {
    const res = await fetch(`${AUTH_SERVICE_BASE}/auth/verify`, {
      headers: {
        cookie: request.headers.get("cookie") ?? "",
        // Withheld in cookieOnly mode so a machine principal cannot
        // authenticate: the sidecar accepts either credential, and the SSH
        // console must be reachable by humans alone.
        "x-api-key": opts.cookieOnly
          ? ""
          : request.headers.get("x-api-key") ?? "",
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

  // ---- SSH console guard (admin humans only; ws exempt — see above) ------
  if (SSH_API_RE.test(pathname) || SSH_PAGE_RE.test(pathname)) {
    if (pathname === SSH_WS_PATH) return NextResponse.next();

    const headers = new Headers(request.headers);
    headers.delete("x-auth-user");
    headers.delete("x-auth-role");

    if (!CONTROL_OPEN) {
      const v = await verifySession(request, { cookieOnly: true });
      if (!v.ok || v.role !== "admin") {
        if (SSH_API_RE.test(pathname)) {
          return NextResponse.json(
            {
              detail: v.ok
                ? "The SSH console is restricted to admins."
                : "Sign in as an admin to open an SSH session.",
            },
            { status: v.ok ? 403 : 401 },
          );
        }
        const url = request.nextUrl.clone();
        url.pathname = "/utils/computers";
        url.search = "";
        return NextResponse.redirect(url);
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
    (CONTROL_PATH_RE.test(pathname) || LABWARE_PATH_RE.test(pathname) || CUSTODY_PATH_RE.test(pathname))
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
    "/api/custody/:path*",
    "/admin/:path*",
    "/api/admin/:path*",
    // Negative lookahead, not "/api/ssh/:path*": the ws path must not reach
    // middleware at all on an upgrade request (see the SSH block above).
    "/api/ssh/((?!ws$).*)",
    "/utils/computers/ssh/:path*",
    "/notebooks/:path*",
  ],
};
