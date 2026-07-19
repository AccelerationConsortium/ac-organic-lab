/** @type {import('next').NextConfig} */
// Use 127.0.0.1 (IPv4) instead of "localhost" — on hosts that resolve
// localhost to ::1 first (most modern Linux), the bare default would
// ECONNREFUSED because uvicorn binds 127.0.0.1 only.
// rewrites() is baked at build time, so this default is what most
// production deploys end up using unless DASHBOARD_API_BASE is set.
const apiBase = process.env.DASHBOARD_API_BASE ?? "http://127.0.0.1:8001";

// go2rtc base URL — drives the `/streams/*` rewrite that the camera tile's
// MsePlayer relies on for live video. In production Caddy fronts this; the
// dev rewrite below is what makes `npm run dev` work end-to-end without a
// reverse proxy.
const go2rtcBase = process.env.GO2RTC_BASE ?? "http://127.0.0.1:1984";

const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
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
      {
        source: "/api/:path*",
        destination: `${apiBase}/api/:path*`,
      },
      {
        source: "/streams/:path*",
        destination: `${go2rtcBase}/:path*`,
      },
    ];
  },
};

export default nextConfig;
