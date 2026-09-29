import type { StandardSummary } from "@/lib/api";
import { recognitionLabel } from "./records";
import type { LaboratoryRecord, LaboratoryStandardOption } from "./types";

export interface LaboratoryFacets {
  cities: string[];
  /** `[value, label]` pairs, as the existing filter select expects. */
  statuses: [string, string][];
}

/**
 * Filter options are derived from the laboratories the directory has loaded, so
 * the selects only ever offer a city or a recognition status that exists in the
 * current dataset. A hard-coded status list would offer values the backend may
 * never return.
 */
export function laboratoryFacets(
  laboratories: LaboratoryRecord[],
): LaboratoryFacets {
  const cities = new Set<string>();
  const statuses = new Set<string>();
  for (const laboratory of laboratories) {
    if (laboratory.city) cities.add(laboratory.city);
    if (laboratory.recognitionStatus) statuses.add(laboratory.recognitionStatus);
  }
  return {
    cities: [...cities].sort((a, b) => a.localeCompare(b)),
    statuses: [...statuses]
      .sort((a, b) => a.localeCompare(b))
      .map((value) => [value, recognitionLabel(value)] as [string, string]),
  };
}

/**
 * Capability suggestions, taken from the records whose detail has loaded.
 *
 * There is no endpoint that enumerates every test name in the catalogue, so the
 * capability filter stays a free-text field (`test`, matched server-side) with
 * suggestions drawn from what is already on screen. Nothing is invented: a
 * suggestion is always a capability that exists in the current dataset.
 */
export function capabilitySuggestions(records: LaboratoryRecord[]) {
  const names = new Set<string>();
  for (const record of records)
    for (const capability of record.capabilities) names.add(capability.name);
  return [...names].sort((a, b) => a.localeCompare(b));
}

/**
 * Standards to offer in the directory filter, narrowed to the ones at least
 * one indexed laboratory is actually recognized for — a standard with no
 * laboratory is not a useful filter value.
 *
 * `keepIds` retains a standard that is active in the URL even when it has no
 * laboratory, so the control keeps showing the filter that is really applied:
 * the Standards page links here by standard id, and a standard with no
 * laboratory legitimately returns nothing.
 */
export function laboratoryStandardOptions(
  pairs: { standard: StandardSummary; laboratories: { id: string }[] }[],
  keepIds: string[] = [],
): LaboratoryStandardOption[] {
  return pairs
    .filter(
      ({ standard, laboratories }) =>
        laboratories.length > 0 || keepIds.includes(standard.id),
    )
    .map(({ standard, laboratories }) => ({
      id: standard.id,
      isNumber: standard.is_number,
      title: standard.title ?? null,
      laboratoryCount: laboratories.length,
    }))
    .sort((a, b) => a.isNumber.localeCompare(b.isNumber, undefined, { numeric: true }));
}
