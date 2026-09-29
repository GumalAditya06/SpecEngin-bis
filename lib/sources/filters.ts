import type { SourceRecord } from "./types";

export interface SourceFilters {
  /** Edition year, as offered by the year facet. */
  year?: string;
  /** Source organization (publisher), as offered by the organization facet. */
  organization?: string;
}

export interface SourceFacets {
  years: number[];
  organizations: string[];
}

/**
 * Facet options are derived from the records the library has loaded, so the
 * filters only ever offer values that exist in the current corpus.
 */
export function sourceFacets(sources: SourceRecord[]): SourceFacets {
  const years = new Set<number>();
  const organizations = new Set<string>();
  for (const source of sources) {
    if (source.year) years.add(source.year);
    if (source.organization) organizations.add(source.organization);
  }
  return {
    years: [...years].sort((a, b) => b - a),
    organizations: [...organizations].sort((a, b) => a.localeCompare(b)),
  };
}

/**
 * Year and organization are refined on the already-fetched page: the search
 * endpoint supports `q`, `source_type` and `licence` only, and no new endpoint
 * is introduced for the other two. Search, type and access stay server-side.
 */
export function filterSources(
  sources: SourceRecord[],
  filters: SourceFilters,
): SourceRecord[] {
  const year = filters.year ? Number(filters.year) : null;
  return sources.filter(
    (source) =>
      (!year || source.year === year) &&
      (!filters.organization || source.organization === filters.organization),
  );
}
