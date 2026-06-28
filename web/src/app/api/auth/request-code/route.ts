import { NextRequest, NextResponse } from "next/server";
import { AUTH_SERVICE_BASE } from "@/lib/auth-service";

// Email a one-time sign-in code. Relays the sidecar's status verbatim so the
// frontend can distinguish 202 (sent), 403 (not allow-listed), 502 (mail
// failure).
export async function POST(request: NextRequest) {
  const body = await request.text();
  try {
    const r = await fetch(`${AUTH_SERVICE_BASE}/auth/request-code`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      cache: "no-store",
    });
    const text = await r.text();
    return new NextResponse(text, {
      status: r.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return NextResponse.json(
      { detail: "Auth service unreachable." },
      { status: 502 },
    );
  }
}
