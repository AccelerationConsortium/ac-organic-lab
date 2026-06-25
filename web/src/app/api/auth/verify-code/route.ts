import { NextRequest, NextResponse } from "next/server";
import { AUTH_SERVICE_BASE } from "@/lib/auth-service";

// Verify the code and adopt the session. The sidecar sets the HttpOnly
// `ac_auth_session` cookie via Set-Cookie on success; we relay that header so
// the browser stores it on the dashboard origin. Subsequent control writes
// then carry it, and the middleware validates it via /auth/verify.
export async function POST(request: NextRequest) {
  const body = await request.text();
  try {
    const r = await fetch(`${AUTH_SERVICE_BASE}/auth/verify-code`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      cache: "no-store",
    });
    const text = await r.text();
    const res = new NextResponse(text, {
      status: r.status,
      headers: { "content-type": "application/json" },
    });
    const setCookie = r.headers.get("set-cookie");
    if (setCookie) res.headers.set("set-cookie", setCookie);
    return res;
  } catch {
    return NextResponse.json(
      { detail: "Auth service unreachable." },
      { status: 502 },
    );
  }
}
