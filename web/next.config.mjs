/** @type {import('next').NextConfig} */
const apiBase = process.env.DASHBOARD_API_BASE ?? "http://localhost:8001";

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
