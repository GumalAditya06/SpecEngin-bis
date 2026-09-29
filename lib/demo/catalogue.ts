import data from "./sample-data.json";
import type {
  ClauseNode,
  DocumentDetail,
  LaboratoryDetail,
  SearchDocument,
  StandardDetail,
} from "../api/types";

export const sourceLabels: Record<string, string> = {
  indigenous_pdf: "Indian Standard",
  qco: "Quality Control Order",
  scheme_reg: "Certification scheme",
  hallmarking_docs: "Hallmarking",
  kys_catalogue: "Standards catalogue",
  lab_lists: "Laboratory list",
  faq_pages: "FAQ",
};
const isoDate = (value: string) => value.replace(" ", "T") + "Z";
const meta = (value: string | null) =>
  JSON.parse(value || "{}") as Record<string, string>;
export const standards = data.standards.map((s) => ({
  ...s,
  created_at: isoDate(s.created_at),
  document_count: data.documents.filter((d) => d.standard_id === s.id).length,
}));
export const labs = data.laboratories.map((l) => ({
  ...l,
  standard_count: data.standard_laboratory.filter(
    (r) => r.laboratory_id === l.id,
  ).length,
}));
export function clauseTree(
  documentId: string,
  parent: string | null = null,
): ClauseNode[] {
  return data.clause_nodes
    .filter((n) => n.document_id === documentId && n.parent_id === parent)
    .sort((a, b) => a.order_index - b.order_index)
    .map((n) => ({ ...n, children: clauseTree(documentId, n.id) }));
}
export const documents: SearchDocument[] = data.documents.map((d) => {
  const s = data.sources.find((s) => s.id === d.source_id)!;
  const m = meta(s.meta);
  return {
    ...d,
    source_type: s.source_type,
    source_type_label: sourceLabels[s.source_type] || s.source_type,
    publisher: s.publisher,
    url: s.url,
    retrieved_at: isoDate(s.retrieved_at),
    department: m.department || null,
    product: m.product || null,
    notified_on: m.notified_on || null,
    effective_on: m.effective_on || null,
    sector: m.sector || null,
    snippet:
      d.licence_class === "full_text_ok"
        ? d.raw_text?.slice(0, 420) || null
        : null,
    clause_count: data.clause_nodes.filter((n) => n.document_id === d.id)
      .length,
    chunk_count: data.chunks.filter((n) => n.document_id === d.id).length,
    standard: standards.find((s) => s.id === d.standard_id) || null,
    clause_tree: clauseTree(d.id),
  };
});
export function standardDetail(id: string): StandardDetail | undefined {
  const s = standards.find((s) => s.id === id);
  if (!s) return;
  return {
    ...s,
    documents: documents.filter((d) => d.standard_id === id),
    amendments: data.amendments.filter((a) => a.standard_id === id),
    related: standards.filter((s) =>
      data.standard_related.some(
        (r) => r.base_standard_id === id && r.related_standard_id === s.id,
      ),
    ),
    laboratories: labs.filter((l) =>
      data.standard_laboratory.some(
        (r) => r.standard_id === id && r.laboratory_id === l.id,
      ),
    ),
    certification_schemes: data.certification_schemes.filter(
      (s) => s.standard_id === id,
    ),
  };
}
export function laboratoryDetail(id: string): LaboratoryDetail | undefined {
  const l = labs.find((l) => l.id === id);
  return l
    ? {
        ...l,
        standards: standards.filter((s) =>
          data.standard_laboratory.some(
            (r) => r.laboratory_id === id && r.standard_id === s.id,
          ),
        ),
        tests: data.tests.filter((t) => t.laboratory_id === id),
      }
    : undefined;
}
export function documentDetail(id: string): DocumentDetail | undefined {
  const d = data.documents.find((d) => d.id === id);
  if (!d) return;
  const s = data.sources.find((s) => s.id === d.source_id)!;
  return {
    ...d,
    full_text: d.licence_class === "full_text_ok" ? d.raw_text : null,
    source: {
      ...s,
      retrieved_at: isoDate(s.retrieved_at),
      source_type_label: sourceLabels[s.source_type] || s.source_type,
      meta: meta(s.meta),
      document_count: data.documents.filter((d) => d.source_id === s.id).length,
    },
    standard: standards.find((s) => s.id === d.standard_id) || null,
    clause_tree: clauseTree(id),
    chunks: data.chunks
      .filter((c) => c.document_id === id)
      .map((c) => ({
        ...c,
        clause_path: meta(c.meta).clause_path || null,
        text: d.licence_class === "full_text_ok" ? c.text : null,
      })),
  };
}
export const sources = data.sources.map((s) => ({
  ...s,
  meta: meta(s.meta),
  content_hash: "demo",
  retrieved_at: isoDate(s.retrieved_at),
  source_type_label: sourceLabels[s.source_type] || s.source_type,
  document_count: data.documents.filter((d) => d.source_id === s.id).length,
}));
export const stats = {
  sources: sources.length,
  documents: documents.length,
  standards: standards.length,
  laboratories: labs.length,
  clause_nodes: data.clause_nodes.length,
  chunks: data.chunks.length,
  by_source_type: Object.entries(sourceLabels).map(([type, label]) => ({
    type,
    label,
    count: sources.filter((s) => s.source_type === type).length,
  })),
  by_licence: ["full_text_ok", "metadata_only"].map((licence_class) => ({
    licence_class,
    count: sources.filter((s) => s.licence_class === licence_class).length,
  })),
};
