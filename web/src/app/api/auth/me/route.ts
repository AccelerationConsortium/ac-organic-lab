import { NextRequest, NextResponse } from "next/server";
import { AUTH_SERVICE_BASE } from "@/lib/auth-service";

// Identity check for the frontend. Forwards the session cookie to the
// sidecar's GET /auth/me and relays its {authenticated, identity} body.
export async function GET(request: NextRequest) {
  try {
    const r = await fetch(`${AUTH_SERVICE_BASE}/auth/me`, {
      headers: { cookie: request.headers.get("cookie") ?? "" },
      cache: "no-store",
    });
    const text = await r.text();
    return new NextResponse(text, {
      status: r.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    // Sidecar unreachable — treat as logged out rather than erroring.
    return NextResponse.json({ authenticated: false, identity: null });
  }
}
