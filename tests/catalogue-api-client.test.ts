import { afterEach, describe, expect, test, vi } from "vitest";

import {
  getDocument,
  getLaboratory,
  getStandard,
  listLaboratories,
  listStandards,
  searchDocuments,
} from "@/lib/api/client";

function response(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("live catalogue client", () => {
  test("uses the same-origin proxy for every catalogue endpoint", async () => {
    const fetch = vi.fn().mockResolvedValue(response({ results: [] }));
    vi.stubGlobal("fetch", fetch);

    await listStandards({ q: "wire rope", year: 2024 });
    await getStandard("standard-1");
    await searchDocuments({ q: "test method", source_type: "indigenous_pdf" });
    await getDocument("document-1");
    await listLaboratories({ city: "Pune", recognition_status: "recognized" });
    await getLaboratory("laboratory-1");

    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      "/api/catalogue/api/v1/standards?q=wire+rope&year=2024&sort=title&order=asc&limit=50&offset=0",
      "/api/catalogue/api/v1/standards/standard-1",
      "/api/catalogue/api/v1/search?q=test+method&source_type=indigenous_pdf&limit=20&offset=0",
      "/api/catalogue/api/v1/documents/document-1",
      "/api/catalogue/api/v1/laboratories?city=Pune&recognition_status=recognized&limit=50&offset=0",
      "/api/catalogue/api/v1/laboratories/laboratory-1",
    ]);
  });

  test("keeps the existing user-safe catalogue errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ detail: "Missing" }, 404)));

    await expect(getStandard("missing")).rejects.toMatchObject({
      status: 404,
      message: "This item could not be found.",
    });
  });
});
