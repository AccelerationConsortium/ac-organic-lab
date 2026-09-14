/** @type {import('next').NextConfig} */
// Use 127.0.0.1 (IPv4) instead of "localhost" — on hosts that resolve
// localhost to ::1 first (most modern Linux), the bare default would
// ECONNREFUSED because uvicorn binds 127.0.0.1 only.
// rewrites() is baked at build time, so this default is what most
// production deploys end up using unless DASHBOARD_API_BASE is set.
const apiBase = process.env.DASHBOARD_API_BASE ?? "http://127.0.0.1:8001";

// Camera viewing uses the authenticated API broker, including on direct
// dashboard URLs. Never rewrite browser paths to the relay management API.

// Unique per build (commit + build time), used as the Next build id so a
// redeploy invalidates the chunk hashes the cached HTML references. It was
// also exported as NEXT_PUBLIC_BITACORA_IFRAME_V to bust the Workflows
// iframe's src; that iframe is gone (Workflows is now a plain link to
// /bitacora), so the export went with it. Keep the build id — the no-store
// header on HTML in Caddyfile.single-edge is the other half of the same fix.
const BUILD_STAMP = `${process.env.GIT_COMMIT ?? "dev"}-${Date.now()}`;

const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  experimental: {
    // The rewrite proxy below is what carries the assistant's SSE stream from
    // FastAPI to the browser, and http-proxy's default proxyTimeout is 30 s of
    // socket INACTIVITY — not total duration. A Control turn that thinks (or
    // waits on a device tool call) for 30 s without emitting a frame used to
    // have its upstream connection aborted mid-answer, which the bubble
    // reports as "Connection lost". The real fix is the API's keep-alive
    // pulse (api/app/assistant_openai.py, IDLE_TICK_S); this is the backstop
    // for it, set above the assistant's own 300 s wallclock cap
    // (api/app/assistant.py DEFAULT_TIMEOUT_S) so a genuinely stuck upstream
    // still gets cut. Takes effect on the next build.
    proxyTimeout: 330_000,
  },
  generateBuildId: () => BUILD_STAMP,
  async redirects() {
    return [
      // The labware builder moved under the Utils section (2026-07-16).
      {
        source: "/labware_builder",
        destination: "/utils/labware_builder",
        permanent: false,
      },
      // API Reference moved under Utils too (2026-07-16).
      {
        source: "/api-reference",
        destination: "/utils/api_reference",
        permanent: false,
      },
    ];
  },
  async rewrites() {
    return [
      // Caddy already serves this shared sign-in surface. Mirror only its
      // public routes on direct dashboard URLs, using our existing session
      // handlers; never expose the sidecar's admin/authz/verification APIs.
      { source: "/auth/banner.js", destination: "/api/auth/banner" },
      ...["me", "users", "login", "verify-code", "logout"].map((path) => ({
        source: `/auth/${path}`, destination: `/api/auth/${path}`,
      })),
      {
        source: "/api/:path*",
        destination: `${apiBase}/api/:path*`,
      },
      {
        source: "/streams/:path*",
        // Legacy/raw relay paths are never a bypass around viewing sessions.
        destination: `${apiBase}/api/camera-streams/legacy-disabled/:path*`,
      },
    ];
  },
};

export default nextConfig;
