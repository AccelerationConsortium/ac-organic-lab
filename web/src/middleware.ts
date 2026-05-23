import { NextRequest, NextResponse } from "next/server";
import { kindBypassesControlGate } from "@/lib/tile-policy";

// -- /api-reference page gate (pre-existing) --------------------------------

const API_REF_COOKIE = "api_ref_auth";
const API_REF_PREFIX = "/api-reference";
const API_REF_UNLOCK = "/api-reference/unlock";

// -- /api/equipment/*/{control,sash}/* gate (new) ---------------------------
//
// Guards POST/DELETE on the dashboard's control passthrough routes. If
// CONTROL_PASSWORD is set, requests without the `control_auth` cookie are
// rejected with 401 (JSON, since these are XHR endpoints). If the env var
// is unset, the dashboard stays fully open - useful for dev or labs that
// rely solely on Tailscale ACLs.
//
// Per-kind bypass: kindBypassesControlGate() lets cameras + env sensors
// through without the cookie (their controls are convenience-only — see
// lib/tile-policy.ts). The id→kind map is fetched from the FastAPI
// backend and cached in-process so the lookup adds ~0ms after the first
// request. On lookup failure we fail closed (require the cookie).

const CONTROL_COOKIE = "control_auth";
const CONTROL_PATH_RE = /^\/api\/equipment\/([^/]+)\/(?:control|sash)(?:\/|$)/;
const CONTROL_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

const DASHBOARD_API_BASE =
  process.env.DASHBOARD_API_BASE ?? "http://127.0.0.1:8001";
const KIND_CACHE_TTL_MS = 60_000;

let kindCache: { byId: Map<string, string>; at: number } | null = null;

async function lookupKind(equipmentId: string): Promise<string | null> {
  const now = Date.now();
  if (!kindCache || now - kindCache.at > KIND_CACHE_TTL_MS) {
    try {
      const res = await fetch(`${DASHBOARD_API_BASE}/api/equipment`, {
        // Bypass any internal caching layer; the API call is cheap.
        cache: "no-store",
      });
      if (res.ok) {
        const data = (await res.json()) as {
          equipment?: Array<{ id?: string; kind?: string }>;
        };
        const byId = new Map<string, string>();
        for (const e of data.equipment ?? []) {
          if (e.id && e.kind) byId.set(e.id, e.kind);
        }
        kindCache = { byId, at: now };
      }
    } catch {
      // Keep stale cache (if any); fall through to null below otherwise.
    }
  }
  return kindCache?.byId.get(equipmentId) ?? null;
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

  // ---- Control-surface guard --------------------------------------------
  if (CONTROL_METHODS.has(request.method)) {
    const match = CONTROL_PATH_RE.exec(pathname);
    if (match) {
      const expected = process.env.CONTROL_PASSWORD;
      if (expected) {
        const equipmentId = decodeURIComponent(match[1]);
        const kind = await lookupKind(equipmentId);
        if (!kindBypassesControlGate(kind)) {
          const cookie = request.cookies.get(CONTROL_COOKIE)?.value;
          if (cookie !== expected) {
            return NextResponse.json(
              { detail: "Control password required" },
              { status: 401 },
            );
          }
        }
      }
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/api-reference/:path*",
    "/api/equipment/:path*",
  ],
};
