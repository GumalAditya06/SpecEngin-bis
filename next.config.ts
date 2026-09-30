import type { NextConfig } from "next";

function catalogueApiOrigin(): string {
  const value = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("NEXT_PUBLIC_API_URL must be an absolute HTTP(S) URL.");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("NEXT_PUBLIC_API_URL must use HTTP or HTTPS.");
  }
  if (url.search || url.hash) {
    throw new Error("NEXT_PUBLIC_API_URL must not include a query string or hash.");
  }
  return url.toString().replace(/\/$/, "");
}

function retrievalApiOrigin(): string {
  const value = process.env.NEXT_PUBLIC_RETRIEVAL_API_URL ?? catalogueApiOrigin();
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("NEXT_PUBLIC_RETRIEVAL_API_URL must be an absolute HTTP(S) URL.");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("NEXT_PUBLIC_RETRIEVAL_API_URL must use HTTP or HTTPS.");
  }
  if (url.search || url.hash) {
    throw new Error("NEXT_PUBLIC_RETRIEVAL_API_URL must not include a query string or hash.");
  }
  return url.toString().replace(/\/$/, "");
}

const nextConfig: NextConfig = {
  // Next 16's experimental native TypeScript CLI cannot parse this project's
  // generated route config reliably in production builds.
  experimental: { useTypeScriptCli: false },
  // Permit this workstation's LAN address to load dev-only assets while
  // testing the local frontend from another device.
  allowedDevOrigins: ["192.168.0.103"],
  // Keep browser catalogue requests same-origin. The external destination is
  // configured at build time alongside the public API URL.
  async rewrites() {
    if (process.env.NEXT_PUBLIC_DEMO_MODE === "true") return [];
    return [{
      source: "/api/catalogue/:path*",
      destination: `${catalogueApiOrigin()}/:path*`,
    }, {
      source: "/api/assistant/:path*",
      destination: `${retrievalApiOrigin()}/:path*`,
    }];
  },
};

export default nextConfig;
