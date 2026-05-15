import { NextRequest, NextResponse } from "next/server";

const COOKIE = "api_ref_auth";

export async function POST(request: NextRequest) {
  const form = await request.formData();
  const password = form.get("password") as string | null;
  const next = (form.get("next") as string | null) ?? "/api-reference";

  const expected = process.env.API_REF_PASSWORD;

  if (!expected || password === expected) {
    const response = NextResponse.redirect(new URL(next, request.url));
    response.cookies.set(COOKIE, password ?? "", {
      httpOnly: true,
      sameSite: "strict",
      // No `secure` flag so it works over plain http on Tailscale.
      path: "/",
      maxAge: 60 * 60 * 24 * 7, // 1 week
    });
    return response;
  }

  // Wrong password — redirect back to unlock with an error flag.
  const url = new URL("/api-reference/unlock", request.url);
  url.searchParams.set("next", next);
  url.searchParams.set("error", "1");
  return NextResponse.redirect(url);
}
