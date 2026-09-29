/** Exact authoritative hosts represented by the verified BIS/LIMS corpus. */
export const TRUSTED_SOURCE_HOSTS = new Set([
  "bis.gov.in",
  "www.bis.gov.in",
  "standards.bis.gov.in",
  "services.bis.gov.in",
  "www.services.bis.gov.in",
  "lims.bis.gov.in",
]);

/** Return the original URL only when it is safe to expose as an external link. */
export function trustedSourceUrl(value: string | null | undefined): string | null {
  if (!value || value !== value.trim()) return null;
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:") return null;
    if (parsed.username || parsed.password) return null;
    if (parsed.port && parsed.port !== "443") return null;
    if (!TRUSTED_SOURCE_HOSTS.has(parsed.hostname.toLowerCase())) return null;
    return value;
  } catch {
    return null;
  }
}
