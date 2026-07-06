import { NextRequest, NextResponse } from "next/server";
import { AUTH_SERVICE_BASE } from "@/lib/auth-service";

// The signed-in user's own equipment→role map (sidecar GET /authz/mine).
// The UI uses it to disable control surfaces the user holds no role on;
// the server-side gate in api/app/control.py is the actual enforcement.
export async function GET(request: NextRequest) {
  try {
    const r = await fetch(`${AUTH_SERVICE_BASE}/authz/mine`, {
      headers: { cookie: request.headers.get("cookie") ?? "" },
      cache: "no-store",
    });
    const text = await r.text();
    return new NextResponse(text, {
      status: r.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    // Sidecar unreachable — the client falls back to flat-role behavior.
    return NextResponse.json({ detail: "auth service unreachable" }, { status: 502 });
  }
}
