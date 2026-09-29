import {
  getLaboratory,
  getStandard,
  getStandardLaboratories,
  listStandards,
} from "@/lib/api/client";
import type { LaboratoryDetail, LaboratorySummary } from "@/lib/api";
import { laboratoryStandardOptions } from "./filters";
import { toLaboratoryRecord } from "./records";
import type { LaboratoryRecord, LaboratoryStandardOption } from "./types";

/**
 * The directory shows what each laboratory can actually test, but the list
 * endpoint returns neither `tests` nor `standards` — only `standard_count`. The
 * detail endpoint is already used by the laboratory page, so the directory
 * resolves the same payload for the page it is about to render instead of
 * asking the backend for a new, overlapping response shape.
 *
 * Only the visible page is resolved, keeping the request count bounded by
 * `PAGE_SIZE`, and a single failed detail degrades that one card rather than
 * the whole list. Once the backend can return capabilities inline, replace this
 * with the list response and the `detailed` flag disappears.
 */
export async function loadLaboratoryRecords(
  summaries: LaboratorySummary[],
): Promise<LaboratoryRecord[]> {
  const details = await Promise.allSettled(
    summaries.map((summary) => getLaboratory(summary.id)),
  );
  const byId = new Map<string, LaboratoryDetail>();
  details.forEach((result, index) => {
    if (result.status === "fulfilled") byId.set(summaries[index].id, result.value);
  });
  return summaries.map((summary) => toLaboratoryRecord(summary, byId.get(summary.id)));
}

/**
 * How many standards are scanned when building the standard filter.
 *
 * The reverse mapping (standard → laboratories) only exists per standard, so
 * the options are derived by scanning the first page of standards. The cap
 * keeps the request count fixed; a backend facet endpoint
 * (`GET /api/v1/laboratories/facets`, or `standards?has_laboratory=true`)
 * should replace it and then the scan can cover the whole catalogue.
 */
const MAX_STANDARD_SCAN = 25;

/**
 * Standards that at least one indexed laboratory is recognized for, with the
 * number of laboratories behind each one.
 *
 * `selectedId` keeps a standard that is applied in the URL but falls outside the
 * scanned page (the Standards page links here by id) in the option list, so the
 * select always shows the filter that is actually active.
 */
export async function loadLaboratoryStandardOptions(
  selectedId?: string | null,
): Promise<LaboratoryStandardOption[]> {
  const { results } = await listStandards({
    limit: MAX_STANDARD_SCAN,
    sort: "title",
    order: "asc",
  });
  const lists = await Promise.allSettled(
    results.map((standard) => getStandardLaboratories(standard.id)),
  );
  const options = laboratoryStandardOptions(
    results.map((standard, index) => ({
      standard,
      laboratories:
        lists[index].status === "fulfilled" ? lists[index].value.laboratories : [],
    })),
  );
  if (!selectedId || options.some((option) => option.id === selectedId))
    return options;
  const [standard, laboratories] = await Promise.allSettled([
    getStandard(selectedId),
    getStandardLaboratories(selectedId),
  ]);
  if (standard.status !== "fulfilled") return options;
  return [
    ...laboratoryStandardOptions(
      [
        {
          standard: standard.value,
          laboratories:
            laboratories.status === "fulfilled"
              ? laboratories.value.laboratories
              : [],
        },
      ],
      [selectedId],
    ),
    ...options,
  ];
}
