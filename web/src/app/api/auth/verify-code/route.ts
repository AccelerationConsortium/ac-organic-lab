import { NextRequest, NextResponse } from "next/server";
import {
  AUTH_COOKIE_DOMAIN,
  AUTH_COOKIE_MAX_AGE_S,
  AUTH_COOKIE_NAME,
  AUTH_SERVICE_BASE,
} from "@/lib/auth-service";

// Verify the code and adopt the session. The sidecar sets the HttpOnly
// `ac_auth_session` cookie via Set-Cookie on success. When AUTH_COOKIE_DOMAIN
// is set we re-issue that token as our own cookie scoped to the parent domain
// (the sidecar's cookie is host-only and Secure, so it neither spans the
// tailnet nor sticks on our plain-http origin); otherwise we relay the header
// verbatim. Subsequent control writes carry it, and the middleware validates
// it via /auth/verify.
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
    const token =
      r.status === 200 && setCookie
        ? new RegExp(`${AUTH_COOKIE_NAME}=([^;,\\s]+)`).exec(setCookie)?.[1]
        : undefined;
    if (AUTH_COOKIE_DOMAIN && token) {
      res.cookies.set(AUTH_COOKIE_NAME, token, {
        domain: AUTH_COOKIE_DOMAIN,
        httpOnly: true,
        sameSite: "lax",
        secure: false,
        path: "/",
        maxAge: AUTH_COOKIE_MAX_AGE_S,
      });
    } else if (setCookie) {
      res.headers.set("set-cookie", setCookie);
    }
    return res;
  } catch {
    return NextResponse.json(
      { detail: "Auth service unreachable." },
      { status: 502 },
    );
  }
}
