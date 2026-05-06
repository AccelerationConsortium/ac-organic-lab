/** @type {import('next').NextConfig} */
// Use 127.0.0.1 (IPv4) instead of "localhost" — on hosts that resolve
// localhost to ::1 first (most modern Linux), the bare default would
// ECONNREFUSED because uvicorn binds 127.0.0.1 only.
// rewrites() is baked at build time, so this default is what most
// production deploys end up using unless DASHBOARD_API_BASE is set.
const apiBase = process.env.DASHBOARD_API_BASE ?? "http://127.0.0.1:8001";

const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiBase}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
