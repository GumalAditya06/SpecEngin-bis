import type { LaboratoryDetail, LaboratorySummary } from "@/lib/api";
import type { RelatedStandardRef } from "@/lib/sources/types";
import type {
  LaboratoryCapability,
  LaboratoryCapabilityGroup,
  LaboratoryRecognitionDetail,
  LaboratoryRecord,
} from "./types";

// ---- Presentation helpers ---------------------------------------------------

/** `recognized` -> "Recognized", `under_review` -> "Under review". */
export function recognitionLabel(status: string | null | undefined) {
  const value = (status || "").trim().replace(/_/g, " ");
  if (!value) return "Status not recorded";
  return value.charAt(0).toUpperCase() + value.slice(1);
}

export function isRecognized(status: string | null | undefined) {
  return (status || "").trim() === "recognized";
}

/** "Mumbai, Maharashtra", or an explicit absence when nothing is recorded. */
export function locationLabel(record: {
  city: string | null;
  state: string | null;
}) {
  const place = [record.city, record.state].filter(Boolean).join(", ");
  return place || "Location not recorded";
}

// ---- Backend gaps, kept as obvious integration points ------------------------

/**
 * Laboratory overview.
 *
 * `GET /api/v1/laboratories` returns no description, scope or profile text, so
 * there is nothing to show. Returning `null` keeps the detail page honest: the
 * overview paragraph is omitted rather than filled with invented prose. Return
 * the backend text here when a description column exists.
 */
export function laboratoryOverview(): null {
  return null;
}

/**
 * Recognition reference and validity.
 *
 * The `laboratories` table stores only `recognition_status` - there is no
 * recognition number, approved-scope text or validity date to show. Returning
 * `undefined` means "the backend cannot say", so the recognition section renders
 * only the fields that exist.
 */
export function laboratoryRecognitionDetail(): undefined {
  return undefined;
}

// ---- Adapters ---------------------------------------------------------------

function toRelatedStandard(standard: {
  id: string;
  is_number: string;
  title: string | null;
}): RelatedStandardRef {
  return {
    id: standard.id,
    is_number: standard.is_number,
    title: standard.title,
  };
}

function toCapability(
  test: LaboratoryDetail["tests"][number],
): LaboratoryCapability {
  return {
    id: test.id,
    name: test.test_name,
    description: test.description,
    standardId: test.standard_id,
  };
}

/**
 * A laboratory summary, with its detail payload when one was loaded.
 *
 * The detail is optional on purpose: the list endpoint cannot return `standards`
 * or `tests`, so the record must stay usable - and honest about what it does not
 * know - before that second request resolves.
 */
export function toLaboratoryRecord(
  summary: LaboratorySummary,
  detail?: LaboratoryDetail,
): LaboratoryRecord {
  return {
    id: summary.id,
    name: summary.name,
    city: summary.city ?? null,
    state: summary.state ?? null,
    address: summary.address ?? null,
    contactEmail: summary.contact_email ?? null,
    recognitionStatus: summary.recognition_status ?? "",
    standardCount: summary.standard_count,
    detailed: Boolean(detail),
    standards: (detail?.standards ?? []).map(toRelatedStandard),
    capabilities: (detail?.tests ?? []).map(toCapability),
    overview: laboratoryOverview(),
    recognitionDetail: laboratoryRecognitionDetail(),
  };
}

// ---- Recognition -------------------------------------------------------------

export interface LaboratoryFact {
  label: string;
  value: string;
}

/**
 * Recognition information, built only from fields the backend actually returns.
 *
 * `recognition_status`, `address` and `contact_email` are the whole
 * `laboratories` model today, so the status, the standard association and the
 * contact details are all that can be stated. The reference / scope / validity
 * rows appear by themselves once `laboratoryRecognitionDetail` starts returning
 * them, with no invented dates and no filler.
 */
export function recognitionFacts(record: LaboratoryRecord): LaboratoryFact[] {
  const facts: LaboratoryFact[] = [
    {
      label: "Recognition status",
      value: recognitionLabel(record.recognitionStatus),
    },
  ];
  const scope = record.standards
    .map((standard) => standard.is_number)
    .filter(Boolean);
  facts.push({
    label: "Recognized standards",
    value: scope.length
      ? scope.join(" · ")
      : record.standardCount
        ? `${record.standardCount} standard${record.standardCount === 1 ? "" : "s"}`
        : "Not recorded",
  });
  facts.push({ label: "Location", value: locationLabel(record) });
  if (record.address) facts.push({ label: "Address", value: record.address });
  if (record.contactEmail)
    facts.push({ label: "Contact", value: record.contactEmail });
  const detail: LaboratoryRecognitionDetail | undefined = record.recognitionDetail;
  if (detail?.reference)
    facts.push({ label: "Recognition reference", value: detail.reference });
  if (detail?.scope) facts.push({ label: "Approved scope", value: detail.scope });
  if (detail?.validFrom)
    facts.push({ label: "Valid from", value: detail.validFrom });
  if (detail?.validUntil)
    facts.push({ label: "Valid until", value: detail.validUntil });
  return facts;
}

// ---- Standard to capability to laboratory -----------------------------------

/** Capabilities recorded against one of the laboratory's standards. */
export function capabilitiesForStandard(
  record: LaboratoryRecord,
  standardId: string | null | undefined,
) {
  if (!standardId) return [];
  return record.capabilities.filter((item) => item.standardId === standardId);
}

/**
 * Capabilities grouped by the standard they belong to.
 *
 * Grouping by standard is the only grouping the data supports: `tests` carry a
 * `standard_id` and nothing else, so an "Electrical safety / Material testing"
 * taxonomy would have to be invented. Capabilities with no resolvable standard
 * are kept in a trailing unlinked group rather than dropped.
 */
export function capabilityGroups(
  record: LaboratoryRecord,
): LaboratoryCapabilityGroup[] {
  const known = new Map(
    record.standards
      .filter((standard) => standard.id)
      .map((standard) => [standard.id as string, standard]),
  );
  const unlinked: LaboratoryCapability[] = [];
  const grouped = new Map<string, LaboratoryCapability[]>();
  for (const capability of record.capabilities) {
    const standard = capability.standardId
      ? known.get(capability.standardId)
      : undefined;
    if (!standard) {
      unlinked.push(capability);
      continue;
    }
    const list = grouped.get(standard.id as string) ?? [];
    list.push(capability);
    grouped.set(standard.id as string, list);
  }
  const groups: LaboratoryCapabilityGroup[] = record.standards
    .filter((standard) => standard.id && grouped.has(standard.id))
    .map((standard) => ({
      standard,
      capabilities: grouped.get(standard.id as string) as LaboratoryCapability[],
    }));
  if (unlinked.length) groups.push({ standard: null, capabilities: unlinked });
  return groups;
}

/** Label for a capability group heading. */
export function capabilityGroupLabel(group: LaboratoryCapabilityGroup) {
  return group.standard?.is_number || "Other listed tests";
}

// ---- Assistant hand-off -----------------------------------------------------

/**
 * The context a laboratory hand-off can carry today.
 *
 * `POST /api/v1/assistant/query` accepts free text only, so the question below
 * is the transport: it names the laboratory, its location and the standard, and
 * the Assistant resolves the rest. Exported so the same object can travel as a
 * structured `laboratory` field once the query payload grows one - the same
 * single-AI-system path the sources hand-off already uses.
 */
export function laboratoryContext(record: LaboratoryRecord) {
  return {
    laboratory_id: record.id,
    laboratory: record.name,
    location: locationLabel(record),
    recognition_status: record.recognitionStatus,
    standards: record.standards
      .map((standard) => standard.is_number)
      .filter(Boolean),
    capabilities: record.capabilities.map((capability) => capability.name),
  };
}

/** One question format for laboratories, matching the sources hand-off. */
export function laboratoryQuestion(
  record: LaboratoryRecord,
  standard?: RelatedStandardRef | null,
) {
  const where = standard?.is_number ? ` for ${standard.is_number}` : "";
  const at = record.city ? ` in ${record.city}` : "";
  return `What testing can ${record.name}${at} perform${where}?`;
}

export function laboratoryAssistantHref(
  record: LaboratoryRecord,
  standard?: RelatedStandardRef | null,
) {
  return `/assistant?q=${encodeURIComponent(laboratoryQuestion(record, standard))}`;
}

/** Accessible name for the Assistant call to action. */
export function laboratoryAssistantLabel(
  record: LaboratoryRecord,
  standard?: RelatedStandardRef | null,
) {
  const scope = standard?.is_number
    ? ` for ${standard.is_number}`
    : record.standardCount
      ? ` for the ${record.standardCount} standard${
          record.standardCount === 1 ? "" : "s"
        } it is recognized for`
      : "";
  return `Ask the Assistant what testing ${record.name} can perform${scope}.`;
}
