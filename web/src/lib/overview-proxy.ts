import { NextRequest, NextResponse } from "next/server";
import { AUTH_SERVICE_BASE } from "@/lib/auth-service";

// Read-only proxy for the Overview page's "Accounts & Activities" headline
// tile: forwards the session cookie to the ac_auth sidecar's /overview/*
// endpoints. These return only aggregate figures, and the sidecar requires a
// valid session of ANY role (unlike /admin/*, which books admin). One literal
// route file per endpoint (app/api/overview/<name>/route.ts) rather than a
// [...path] catch-all: the next.config `/api/:path*` rewrite to the FastAPI
// dashboard is applied BEFORE dynamic routes, so only literal routes win.
export function overviewProxy(endpoint: string) {
  return async function GET(request: NextRequest) {
    const search = request.nextUrl.search;
    try {
      const r = await fetch(`${AUTH_SERVICE_BASE}/overview/${endpoint}${search}`, {
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
