import { NextResponse } from "next/server";
import { AUTH_SERVICE_BASE } from "@/lib/auth-service";

export const dynamic = "force-dynamic";

/** Public static login asset; the sidecar address stays runtime-configured. */
export async function GET() {
  try {
    const response = await fetch(`${AUTH_SERVICE_BASE}/auth/banner.js`, {
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(5000),
    });
    const media = response.headers.get("content-type")?.split(";", 1)[0].trim();
    if (!response.ok || !["application/javascript", "text/javascript"].includes(media ?? "")) {
      throw new Error("Unexpected auth banner response");
    }
    const text = await response.text();
    if (new TextEncoder().encode(text).length > 128 * 1024) throw new Error("Auth banner too large");
    return new NextResponse(text, { headers: {
      "Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    } });
  } catch {
    return NextResponse.json({ detail: "SDL2 sign-in banner unavailable." }, {
      status: 503, headers: { "Cache-Control": "no-store" },
    });
  }
}
