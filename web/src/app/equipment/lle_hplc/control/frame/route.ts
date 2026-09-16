import { NextRequest, NextResponse } from "next/server";
import { AUTH_COOKIE_NAME, AUTH_SERVICE_BASE } from "@/lib/auth-service";
import previewDocument from "./preview-document.json";

export const dynamic = "force-dynamic";

const PRIVATE_HEADERS = {
  "Cache-Control": "private, no-store",
  "Vary": "Cookie",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
};

/** A static simulation for signed-in humans. No device or control API is called. */
export async function GET(request: NextRequest) {
  const cookie = request.cookies.get(AUTH_COOKIE_NAME);
  if (!cookie?.value) return NextResponse.json({ detail: "Sign in with SDL2 to open the HPLC control preview." }, { status: 401, headers: PRIVATE_HEADERS });

  let verified: Response;
  try {
    verified = await fetch(`${AUTH_SERVICE_BASE}/auth/verify`, {
      headers: { cookie: `${AUTH_COOKIE_NAME}=${encodeURIComponent(cookie.value)}` },
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(5000),
    });
  } catch {
    return NextResponse.json({ detail: "SDL2 sign-in verification is unavailable." }, { status: 503, headers: PRIVATE_HEADERS });
  }
  if (verified.status === 401 || verified.status === 403) return NextResponse.json({ detail: "Your SDL2 session has expired. Sign in again." }, { status: 401, headers: PRIVATE_HEADERS });
  if (!verified.ok || !verified.headers.get("x-auth-user")) return NextResponse.json({ detail: "SDL2 sign-in verification is unavailable." }, { status: 503, headers: PRIVATE_HEADERS });

  return new NextResponse(previewDocument, { headers: {
    ...PRIVATE_HEADERS,
    "Content-Type": "text/html; charset=utf-8",
    "X-Frame-Options": "SAMEORIGIN",
    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; frame-src 'self'; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'; sandbox allow-scripts",
  } });
}
