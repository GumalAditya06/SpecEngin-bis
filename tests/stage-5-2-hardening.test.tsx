import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import SourceMetadata from "@/components/sources/SourceMetadata";
import { resolveDemoMode } from "@/lib/api/config";
import type { SourceRecord } from "@/lib/sources/types";
import { trustedSourceUrl } from "@/lib/sources/url";

function source(url: string): SourceRecord {
  return {
    id: "source-1", sourceId: "provenance-1", title: "BIS source",
    category: "Indian Standard", organization: "Bureau of Indian Standards",
    version: null, isNumber: "IS 367:1993", publishedOn: null,
    access: "metadata_only", clauseCount: null, retrievedAt: null, url,
    description: null, sector: null, year: 1993, relatedStandards: [],
  };
}

describe("production frontend configuration", () => {
  test("demo data is enabled only by the explicit true value", () => {
    expect(resolveDemoMode("true")).toBe(true);
    expect(resolveDemoMode("false")).toBe(false);
    expect(resolveDemoMode(undefined)).toBe(false);
    expect(resolveDemoMode("TRUE")).toBe(false);
    expect(resolveDemoMode("1")).toBe(false);
  });
});

describe("authoritative source URL allow-list", () => {
  test.each([
    "https://bis.gov.in/source.pdf",
    "https://www.bis.gov.in/source.pdf",
    "https://standards.bis.gov.in/source.pdf",
    "https://services.bis.gov.in/source.pdf",
    "https://www.services.bis.gov.in/source.pdf",
    "https://lims.bis.gov.in/source.pdf",
  ])("allows %s without rewriting it", (url) => {
    expect(trustedSourceUrl(url)).toBe(url);
  });

  test.each([
    "javascript:alert(1)",
    "http://www.bis.gov.in/source.pdf",
    "https://www.bis.gov.in.evil.example/source.pdf",
    "https://evil.example/?next=www.bis.gov.in",
    "https://user:password@www.bis.gov.in/source.pdf",
    "https://www.bis.gov.in:8443/source.pdf",
    " /relative/path",
  ])("rejects %s", (url) => {
    expect(trustedSourceUrl(url)).toBeNull();
  });

  test("fails closed in the shared source metadata component", () => {
    const { rerender } = render(
      <SourceMetadata source={source("https://evil.example/source.pdf")} />,
    );
    expect(screen.queryByRole("link", { name: "Open original source →" })).toBeNull();
    rerender(
      <SourceMetadata source={source("https://standards.bis.gov.in/source.pdf")} />,
    );
    expect(
      screen.getByRole("link", { name: "Open original source →" }).getAttribute("href"),
    ).toBe("https://standards.bis.gov.in/source.pdf");
  });
});
