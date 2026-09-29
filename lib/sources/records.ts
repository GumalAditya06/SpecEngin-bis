import type {
  AssistantCitation,
  ClauseNode,
  DocChunk,
  DocumentDetail,
  SearchDocument,
} from "@/lib/api";
import type {
  RelatedStandardRef,
  SourceClause,
  SourceClauseNode,
  SourceReading,
  SourceRecord,
} from "./types";

// ---- Labels and option sets -------------------------------------------------

/** Source types served by `GET /api/v1/types` and the `/api/v1/search` filter. */
export const SOURCE_TYPE_OPTIONS: [string, string][] = [
  ["indigenous_pdf", "Indian standards"],
  ["qco", "Quality Control Orders"],
  ["scheme_reg", "Certification schemes"],
  ["hallmarking_docs", "Hallmarking"],
  ["lab_lists", "Laboratory lists"],
  ["kys_catalogue", "Catalogue metadata"],
  ["faq_pages", "FAQ pages"],
  ["ISO_adopted", "ISO-adopted standards"],
];

export const ACCESS_OPTIONS: [string, string][] = [
  ["full_text_ok", "Full text available"],
  ["metadata_only", "Metadata only"],
];

export function isFullText(access: string) {
  return access === "full_text_ok";
}

export function accessLabel(access: string) {
  return ACCESS_OPTIONS.find(([value]) => value === access)?.[1] ?? "Access not recorded";
}

/** Editorial fallback used when a source carries no extractable description. */
export const DESCRIPTION_FALLBACK =
  "Open the record for bibliographic details and the original source.";

// ---- Derived metadata -------------------------------------------------------

/**
 * Edition year of a standard reference, e.g. "IS 8921:2024" → 2024.
 * The backend stores no separate year on a document, so the year filter is
 * derived from the reference itself: the year is the edition suffix, never the
 * standard number ("IS 12034:2025" is the 2025 edition, not 2034). Records
 * without a dated reference (notifications, catalogues) have no year and are
 * excluded from year facets.
 */
export function editionYear(isNumber: string | null | undefined) {
  if (!isNumber) return null;
  const edition = /:\s*(\d{4})(?!\d)/.exec(isNumber);
  return edition ? Number(edition[1]) : null;
}

export function formatDate(value: string | null | undefined) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? null
    : date.toLocaleDateString("en-IN", {
        day: "numeric",
        month: "short",
        year: "numeric",
      });
}

/**
 * Specengine references for a source or clause.
 *
 * The backend has no persisted answer, citation or evidence index — assistant
 * citations are produced per response and never stored — so this relationship
 * cannot be determined yet. Returning `undefined` keeps the UI honest: the
 * "Referenced by Specengine" section is hidden rather than faked. Return a
 * `SourceReference[]` once a citation index exists (for example
 * `GET /api/v1/documents/:id/references`) and the section appears on its own.
 */
export function specengineReferences(): undefined {
  return undefined;
}

// ---- Adapters ---------------------------------------------------------------

function relatedStandard(
  standard:
    | { id?: string | null; is_number?: string | null; title?: string | null }
    | null
    | undefined,
): RelatedStandardRef[] {
  if (!standard?.id && !standard?.is_number) return [];
  return [
    {
      id: standard.id ?? null,
      is_number: standard.is_number ?? null,
      title: standard.title ?? null,
    },
  ];
}

function metaValue(
  meta: Record<string, unknown> | null | undefined,
  key: string,
) {
  const value = meta?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function firstSentence(text: string | null | undefined) {
  if (!text) return null;
  const trimmed = text.trim().replace(/\s+/g, " ");
  if (!trimmed) return null;
  return trimmed.length > 280 ? `${trimmed.slice(0, 277)}…` : trimmed;
}

/** List payload from `GET /api/v1/search`. */
export function toSourceRecord(document: SearchDocument): SourceRecord {
  return {
    id: document.id,
    // `/api/v1/search` does not return the source id; only the detail does.
    sourceId: null,
    title: document.title,
    category: document.source_type_label,
    organization: document.publisher,
    version: document.version ?? null,
    access: document.licence_class,
    isNumber: document.is_number,
    description: document.snippet,
    url: document.url,
    retrievedAt: document.retrieved_at,
    publishedOn: document.notified_on || document.effective_on || null,
    sector: document.sector,
    clauseCount: document.clause_count ?? null,
    year: editionYear(document.is_number),
    relatedStandards: relatedStandard(document.standard),
  };
}

function clauseTitle(detail: DocumentDetail, chunk: DocChunk) {
  return (
    detail.clause_tree.find((node) => node.clause_path === chunk.clause_path)
      ?.title ?? null
  );
}

function clauseDepth(detail: DocumentDetail, chunk: DocChunk) {
  return (
    detail.clause_tree.find((node) => node.clause_path === chunk.clause_path)
      ?.depth ?? 1
  );
}

/** Detail payload from `GET /api/v1/documents/:id`. */
export function toSourceRecordFromDetail(
  detail: DocumentDetail,
): SourceRecord {
  const full = isFullText(detail.licence_class);
  return {
    id: detail.id,
    sourceId: detail.source?.id ?? null,
    title: detail.title,
    category: detail.source?.source_type_label ?? "Source",
    organization: detail.source?.publisher ?? "",
    version: detail.version ?? null,
    access: detail.licence_class,
    isNumber: detail.is_number,
    // Licence-gated: a metadata-only record never carries extractable text.
    description: full ? firstSentence(detail.chunks[0]?.text) : null,
    url: detail.source?.url ?? null,
    retrievedAt: detail.source?.retrieved_at ?? null,
    publishedOn:
      metaValue(detail.source?.meta, "notified_on") ??
      metaValue(detail.source?.meta, "effective_on"),
    sector: metaValue(detail.source?.meta, "sector"),
    clauseCount: detail.clause_tree.length || detail.chunks.length,
    year: editionYear(detail.is_number),
    relatedStandards: relatedStandard(detail.standard),
  };
}

function toClauseNodes(
  nodes: ClauseNode[],
  anchorFor: (clausePath: string | null) => number | null,
): SourceClauseNode[] {
  return nodes.map((node) => ({
    number: node.clause_path,
    title: node.title,
    depth: node.depth,
    anchor: anchorFor(node.clause_path),
    children: toClauseNodes(node.children, anchorFor),
  }));
}

/** Full reading view: metadata, clause tree, clause text, full text. */
export function toSourceReading(detail: DocumentDetail): SourceReading {
  const full = isFullText(detail.licence_class);
  const clauses: SourceClause[] = detail.chunks.map((chunk) => ({
    index: chunk.chunk_index,
    number: chunk.clause_path,
    title: clauseTitle(detail, chunk),
    text: full ? chunk.text : null,
    depth: clauseDepth(detail, chunk),
    referencedBy: specengineReferences(),
  }));
  const anchorFor = (clausePath: string | null) =>
    clauses.find((clause) => clause.number === clausePath)?.index ?? null;
  const clauseTree = detail.clause_tree.length
    ? toClauseNodes(detail.clause_tree, anchorFor)
    : // No hierarchy was extracted: fall back to the parsed clause order.
      clauses.map((clause) => ({
        number: clause.number,
        title: clause.title,
        depth: 1,
        anchor: clause.index,
        children: [],
      }));
  return {
    ...toSourceRecordFromDetail(detail),
    clauses,
    clauseTree,
    fullText: full ? detail.full_text : null,
    referencedBy: specengineReferences(),
  };
}

/** Assistant citation. Carries no publisher or version, so those stay unset. */
export function sourceFromCitation(citation: AssistantCitation): SourceRecord {
  return {
    id: citation.document_id,
    sourceId: citation.source_id,
    title: citation.title ?? "",
    // The grounded-answer contract does not currently expose these fields.
    // Leave them empty so the shared drawer omits them instead of inventing
    // document metadata.
    category: "",
    organization: "",
    version: citation.version,
    access: citation.licence_class ?? "",
    isNumber: citation.standard_number,
    description: citation.excerpt,
    url: citation.url,
    retrievedAt: null,
    publishedOn: null,
    sector: null,
    clauseCount: null,
    year: editionYear(citation.standard_number),
    relatedStandards: relatedStandard({
      is_number: citation.standard_number,
    }),
  };
}

// ---- Navigation and Assistant links ----------------------------------------

export function sourceHref(source: SourceRecord, clause?: string | null) {
  if (!source.id) return null;
  const query = clause ? `?clause=${encodeURIComponent(clause)}` : "";
  return `/sources/${source.id}${query}`;
}

/**
 * The single Assistant question format for sources and clauses. The Assistant
 * reads `?q=` as its initial query, so contextual hand-off needs no new
 * endpoint: the source stays the evidence object, the Assistant interprets it.
 */
export function sourceQuestion(
  reference: string,
  clause?: string | null,
  page?: string | number | null,
) {
  const where = clause ? ` clause ${clause}` : "";
  const at = page != null && page !== "" ? ` (page ${page})` : "";
  return `What does ${reference}${where}${at} require?`;
}

export function assistantHref(
  source: SourceRecord,
  clause?: string | null,
  page?: string | number | null,
) {
  return `/assistant?q=${encodeURIComponent(
    sourceQuestion(source.isNumber || source.title, clause, page),
  )}`;
}
