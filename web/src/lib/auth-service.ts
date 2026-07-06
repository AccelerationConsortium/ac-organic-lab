// Base URL of the ac_auth sidecar (loopback on the dashboard host).
// The `/api/*` Next rewrite targets the FastAPI dashboard (:8001), so the
// auth proxy route handlers under app/api/auth/* reach the sidecar directly
// through this constant instead. Override with AUTH_SERVICE_BASE.
export const AUTH_SERVICE_BASE =
  process.env.AUTH_SERVICE_BASE ?? "http://127.0.0.1:8009";

// Name of the shared session cookie the ac_auth sidecar issues. Fixed by the
// sidecar contract — every lab UI validating against the sidecar uses it.
export const AUTH_COOKIE_NAME = "ac_auth_session";

// When set (deploy with tail6a1dd7.ts.net), verify-code re-issues the session
// cookie on the dashboard's own origin scoped to this parent domain, so one
// sign-in covers every *.<domain> lab UI and logout clears it everywhere.
// Unset -> the sidecar's Set-Cookie is relayed verbatim (host-only cookie;
// dev/local behaviour unchanged).
export const AUTH_COOKIE_DOMAIN =
  process.env.AUTH_COOKIE_DOMAIN?.trim() || undefined;

// The dashboard is plain http over the Tailnet, so the re-issued cookie
// cannot be Secure. Lifetime mirrors the sidecar's ~12 h session window.
export const AUTH_COOKIE_MAX_AGE_S = 12 * 3600;
