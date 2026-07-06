import { NextRequest, NextResponse } from "next/server";
import {
  AUTH_COOKIE_DOMAIN,
  AUTH_COOKIE_NAME,
  AUTH_SERVICE_BASE,
} from "@/lib/auth-service";

// Clear the shared session cookie with the SAME Domain verify-code issued it
// with — a delete whose Domain doesn't match leaves the cookie in place.
function clearSessionCookie(res: NextResponse) {
  res.cookies.set(AUTH_COOKIE_NAME, "", {
    domain: AUTH_COOKIE_DOMAIN,
    httpOnly: true,
    sameSite: "lax",
    secure: false,
    path: "/",
    maxAge: 0,
  });
}

// Revoke the session and clear the cookie. Forwards the session cookie so the
// sidecar can delete the right row. When AUTH_COOKIE_DOMAIN is set we clear
// our parent-domain cookie ourselves (the sidecar's clearing Set-Cookie is
// host-only and wouldn't match); otherwise we relay the sidecar's header.
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
    if (AUTH_COOKIE_DOMAIN) {
      clearSessionCookie(res);
    } else {
      const setCookie = r.headers.get("set-cookie");
      if (setCookie) res.headers.set("set-cookie", setCookie);
    }
    return res;
  } catch {
    // Revocation is best-effort; the local cookie still dies.
    const res = NextResponse.json({ ok: true });
    if (AUTH_COOKIE_DOMAIN) clearSessionCookie(res);
    return res;
  }
}
