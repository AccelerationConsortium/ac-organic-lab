import { NextRequest, NextResponse } from "next/server";
import { AUTH_SERVICE_BASE } from "@/lib/auth-service";

// Read-only proxy for the admin page: forwards the session cookie to the
// ac_auth sidecar's /admin/* endpoints. The middleware has already required
// an admin session for /api/admin/*, and the sidecar enforces admin again on
// every endpoint (that check is the authority; this file is plumbing).
//
// One literal route file per endpoint (app/api/admin/<name>/route.ts) rather
// than a [...path] catch-all: the next.config `/api/:path*` rewrite to the
// FastAPI dashboard is applied BEFORE dynamic routes, so a catch-all here
// would never be reached — only literal routes win over the rewrite.
export function adminProxy(endpoint: string) {
  return async function GET(request: NextRequest) {
    const search = request.nextUrl.search;
    try {
      const r = await fetch(`${AUTH_SERVICE_BASE}/admin/${endpoint}${search}`, {
        headers: { cookie: request.headers.get("cookie") ?? "" },
        cache: "no-store",
      });
      const text = await r.text();
      return new NextResponse(text, {
        status: r.status,
        headers: { "content-type": "application/json" },
      });
    } catch {
      return NextResponse.json(
        { detail: "Auth sidecar unreachable." },
        { status: 502 },
      );
    }
  };
}
