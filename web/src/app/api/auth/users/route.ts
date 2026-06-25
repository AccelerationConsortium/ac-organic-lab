import { NextResponse } from "next/server";
import { AUTH_SERVICE_BASE } from "@/lib/auth-service";

// Active human accounts for the login dropdown. Service accounts are
// excluded by the sidecar. Tailnet-gated like the rest of the sidecar.
export async function GET() {
  try {
    const r = await fetch(`${AUTH_SERVICE_BASE}/auth/users`, {
      cache: "no-store",
    });
    const text = await r.text();
    return new NextResponse(text, {
      status: r.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return NextResponse.json({ users: [] });
  }
}
