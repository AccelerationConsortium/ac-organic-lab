import { NextRequest, NextResponse } from "next/server";

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

const CONTROL_COOKIE = "control_auth";
const CONTROL_PATH_RE = /^\/api\/equipment\/[^/]+\/(?:control|sash)(?:\/|$)/;
const CONTROL_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function middleware(request: NextRequest) {
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
  if (
    CONTROL_METHODS.has(request.method) &&
    CONTROL_PATH_RE.test(pathname)
  ) {
    const expected = process.env.CONTROL_PASSWORD;
    if (expected) {
      const cookie = request.cookies.get(CONTROL_COOKIE)?.value;
      if (cookie !== expected) {
        return NextResponse.json(
          { detail: "Control password required" },
          { status: 401 },
        );
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
