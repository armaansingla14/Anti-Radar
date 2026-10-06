import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  devIndicators: false,
  // BACKEND_URL is read server-side only (route handler + server components); nothing is exposed via `env`.
};

export default nextConfig;
