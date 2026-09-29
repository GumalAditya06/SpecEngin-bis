import { getStandard } from "@/lib/api/client";
import { toSourceRecord } from "./records";
import type { SourceRecord } from "./types";

/** Standards are resolved one at a time to keep the request count bounded. */
const MAX_STANDARDS = 6;

/**
 * Source documents behind a set of standards, using the existing standard
 * endpoint. Used by Services and Laboratories so both can offer the same
 * source drawer without a new backend route.
 */
export async function loadSourcesForStandards(
  standardIds: (string | null | undefined)[],
): Promise<SourceRecord[]> {
  const ids = [...new Set(standardIds.filter((id): id is string => Boolean(id)))]
    .slice(0, MAX_STANDARDS);
  if (!ids.length) return [];
  const results = await Promise.allSettled(ids.map((id) => getStandard(id)));
  const seen = new Set<string>();
  const records: SourceRecord[] = [];
  for (const result of results) {
    if (result.status !== "fulfilled") continue;
    for (const document of result.value.documents) {
      if (seen.has(document.id)) continue;
      seen.add(document.id);
      records.push(toSourceRecord(document));
    }
  }
  return records;
}
