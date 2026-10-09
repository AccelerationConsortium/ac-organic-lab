import { AUTH_COOKIE_NAME, AUTH_SERVICE_BASE } from "./auth-service";

/** Server-only policy, shared by middleware and the dynamic page. No dev bypass. */
export async function dashboardPreviewAccess(session?: string): Promise<200 | 401 | 403 | 404> {
  if (process.env.DASHBOARD_SIDEBAR_PREVIEW !== "true") return 404;
  if (!session) return 401;
  try {
    const response = await fetch(`${AUTH_SERVICE_BASE}/auth/verify`, {
      headers: { cookie: `${AUTH_COOKIE_NAME}=${session}` },
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok || !response.headers.get("x-auth-user")) return 401;
    return response.headers.get("x-auth-role") === "admin" ? 200 : 403;
  } catch {
    // Auth outages fail closed; never mount private preview content.
    return 401;
  }
}
