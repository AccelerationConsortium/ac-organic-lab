import { NextRequest, NextResponse } from "next/server";
import { AUTH_SERVICE_BASE } from "@/lib/auth-service";

export const dynamic = "force-dynamic";

// Also used by Caddy forward_auth for every beta page/asset/API request.
// Link visibility is convenience; this cookie-only check is the access gate.
export async function GET(request: NextRequest) {
  const tester = process.env.BITACORA_BETA_TESTER_EMAIL?.trim().toLowerCase();
  const deny = (status: number) => NextResponse.json(
    { detail: "Private beta access is unavailable." },
    { status, headers: { "cache-control": "no-store" } },
  );
  if (!tester) return deny(404);
  const cookie = request.headers.get("cookie");
  if (!cookie) return deny(401);
  try {
    const verified = await fetch(`${AUTH_SERVICE_BASE}/auth/verify`, {
      headers: { cookie }, cache: "no-store", redirect: "manual",
      signal: AbortSignal.timeout(5000),
    });
    if (!verified.ok) return deny(401);
    const user = verified.headers.get("x-auth-user")?.trim().toLowerCase();
    const role = verified.headers.get("x-auth-role")?.trim().toLowerCase();
    if (user !== tester || role !== "admin") return deny(403);
    const headers = new Headers({ "cache-control": "no-store" });
    for (const name of ["X-Auth-User", "X-Auth-Role", "X-Auth-Projects", "X-Auth-Pi-Projects"]) {
      headers.set(name, verified.headers.get(name) ?? "");
    }
    return NextResponse.json({ href: "/bitacora-beta", label: "Bitácora Beta", user }, { headers });
  } catch {
    return deny(503);
  }
}
