import { NextRequest, NextResponse } from "next/server";

/**
 * Lightweight password gate for the dashboard's control surfaces (sash
 * moves, plug toggles, future tiles). Mirrors the api-ref-auth pattern
 * already used for the /api-reference docs page.
 *
 * - `POST {password}`: if `CONTROL_PASSWORD` is set and matches, set an
 *   HttpOnly cookie that the middleware accepts on subsequent control
 *   POSTs. If the env var is unset, the cookie is set unconditionally
 *   (the middleware passes through too, so the dashboard stays open).
 * - `GET`: returns `{enabled, authenticated}` so the frontend can skip
 *   the password modal when no env var is configured.
 *
 * The cookie is intentionally NOT `secure` because the dashboard is
 * reached over plain HTTP on the Tailnet (Tailscale wraps the transport
 * in WireGuard; browsers won't apply `secure` over http://). If the
 * dashboard ever moves behind real TLS, flip `secure: true`.
 */

const COOKIE = "control_auth";
const COOKIE_MAX_AGE_SECONDS = 60 * 30; // 30 min

export async function GET(request: NextRequest) {
  const expected = process.env.CONTROL_PASSWORD;
  const enabled = Boolean(expected);
  const authenticated =
    !enabled || request.cookies.get(COOKIE)?.value === expected;
  return NextResponse.json({ enabled, authenticated });
}

export async function POST(request: NextRequest) {
  let body: { password?: unknown } | null = null;
  try {
    body = (await request.json()) as { password?: unknown };
  } catch {
    body = null;
  }
  const password = typeof body?.password === "string" ? body.password : "";

  const expected = process.env.CONTROL_PASSWORD;

  if (expected && password !== expected) {
    return NextResponse.json(
      { ok: false, error: "Wrong password" },
      { status: 401 },
    );
  }

  // Either no password configured (open mode) or correct password.
  const response = NextResponse.json({ ok: true });
  response.cookies.set(COOKIE, expected ?? "open", {
    httpOnly: true,
    sameSite: "strict",
    path: "/",
    maxAge: COOKIE_MAX_AGE_SECONDS,
  });
  return response;
}
