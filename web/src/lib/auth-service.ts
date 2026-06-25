// Base URL of the ac_auth sidecar (loopback on the dashboard host).
// The `/api/*` Next rewrite targets the FastAPI dashboard (:8001), so the
// auth proxy route handlers under app/api/auth/* reach the sidecar directly
// through this constant instead. Override with AUTH_SERVICE_BASE.
export const AUTH_SERVICE_BASE =
  process.env.AUTH_SERVICE_BASE ?? "http://127.0.0.1:8009";
