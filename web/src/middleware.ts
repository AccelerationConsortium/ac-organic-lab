import { NextRequest, NextResponse } from "next/server";

const COOKIE = "api_ref_auth";
const PREFIX = "/api-reference";
const UNLOCK = "/api-reference/unlock";

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Only guard /api-reference/* — skip the unlock page itself to avoid loops.
  if (!pathname.startsWith(PREFIX) || pathname.startsWith(UNLOCK)) {
    return NextResponse.next();
  }

  const password = process.env.API_REF_PASSWORD;

  // No password configured → page is open.
  if (!password) return NextResponse.next();

  const cookie = request.cookies.get(COOKIE)?.value;
  if (cookie === password) return NextResponse.next();

  // Not authenticated — redirect to unlock, carrying the original URL as
  // `next` so the unlock page can redirect back after a successful login.
  const url = request.nextUrl.clone();
  url.pathname = UNLOCK;
  url.searchParams.set("next", pathname);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/api-reference/:path*"],
};
