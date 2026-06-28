import { NextRequest, NextResponse } from "next/server";
import { AUTH_SERVICE_BASE } from "@/lib/auth-service";

// Revoke the session and clear the cookie. Forwards the session cookie so the
// sidecar can delete the right row, and relays its cookie-clearing Set-Cookie.
export async function POST(request: NextRequest) {
  try {
    const r = await fetch(`${AUTH_SERVICE_BASE}/auth/logout`, {
      method: "POST",
      headers: { cookie: request.headers.get("cookie") ?? "" },
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
    return NextResponse.json({ ok: true });
  }
}
