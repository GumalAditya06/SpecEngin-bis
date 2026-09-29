import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Next 16's experimental native TypeScript CLI cannot parse this project's
  // generated route config reliably in production builds.
  experimental: { useTypeScriptCli: false },
};

export default nextConfig;
