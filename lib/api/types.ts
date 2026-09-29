// Typed mirrors of every backend API response shape.
// Keep these in sync with backend/app/api/routes.py.

// ---- Source / Document (existing) ----

export interface SourceSummary {
  id: string;
  url: string;
  title: string;
  publisher: string;
  source_type: string;
  source_type_label: string;
  licence_class: string;
  retrieved_at: string | null;
  meta: Record<string, unknown> | null;
  document_count: number;
}

export interface SearchDocument {
  id: string;
  is_number: string | null;
  title: string;
  version: number;
  source_type: string;
  source_type_label: string;
  licence_class: string;
  publisher: string;
  url: string;
  retrieved_at: string | null;
  department: string | null;
  product: string | null;
  notified_on: string | null;
  effective_on: string | null;
  sector: string | null;
  snippet: string | null;
  clause_count: number;
  chunk_count: number;
  standard_id: string | null;
  standard: StandardSummary | null;
  clause_tree?: ClauseNode[];
}

export interface SearchResponse {
  total: number;
  limit: number;
  offset: number;
  results: SearchDocument[];
}

// ---- Retrieval evidence (Stage 3.4) ----

export interface RetrievalFilters {
  standard_number?: string;
  document_id?: string;
  clause_number?: string;
  annex_identifier?: string;
  document_type?: string;
  product?: string;
}

export interface RetrievalEvidenceSource {
  document_id: string | null;
  source_id: string | null;
  title: string | null;
  version: string | number | null;
  page: number | null;
  url: string | null;
}

export interface RetrievalEvidenceMetadata {
  methods: ("semantic" | "bm25")[];
  semantic_rank: number | null;
  bm25_rank: number | null;
  rrf_score: number;
  reranker_score: number;
}

export interface RetrievalEvidence {
  rank: number;
  chunk_id: string;
  standard_number: string | null;
  clause_number: string | null;
  clause_title: string | null;
  text: string;
  context_prefix: string | null;
  source: RetrievalEvidenceSource;
  retrieval: RetrievalEvidenceMetadata;
}

export interface RetrievalResponse {
  query: string;
  results: RetrievalEvidence[];
}

// ---- Standard (new) ----

export interface StandardSummary {
  id: string;
  is_number: string;
  title: string;
  status: string;
  sector: string | null;
  year: number | null;
  description: string | null;
  document_count: number;
  created_at: string | null;
}

export interface StandardDetail extends StandardSummary {
  documents: SearchDocument[];
  amendments: Amendment[];
  related: StandardSummary[];
  laboratories: LaboratorySummary[];
  certification_schemes: { id: string; title: string }[];
}

export interface StandardsResponse {
  total: number;
  limit: number;
  offset: number;
  results: StandardSummary[];
}

// ---- Amendment (new) ----

export interface Amendment {
  id: string;
  amendment_number: string;
  title: string | null;
  effective_date: string | null;
}

// ---- Laboratory (new) ----

export interface LaboratorySummary {
  id: string;
  name: string;
  city: string | null;
  state: string | null;
  recognition_status: string;
  address: string | null;
  contact_email: string | null;
  standard_count: number;
}

export interface LaboratoryDetail extends LaboratorySummary {
  standards: StandardSummary[];
  tests: {
    id: string;
    test_name: string;
    description: string | null;
    standard_id: string | null;
  }[];
}

export interface LaboratoriesResponse {
  total: number;
  limit: number;
  offset: number;
  results: LaboratorySummary[];
}

// ---- Source (new /api/v1/sources) ----

export interface SourceEntry {
  id: string;
  url: string;
  title: string;
  publisher: string;
  source_type: string;
  source_type_label: string;
  licence_class: string;
  retrieved_at: string | null;
  content_hash: string;
  meta: Record<string, unknown>;
  document_count: number;
}

export interface SourcesResponse {
  total: number;
  limit: number;
  offset: number;
  sources: SourceEntry[];
}

// ---- Stats (enhanced) ----

export interface StatsBySourceType {
  type: string;
  label: string;
  count: number;
}

export interface StatsByLicence {
  licence_class: string;
  count: number;
}

export interface Stats {
  sources: number;
  documents: number;
  clause_nodes: number;
  chunks: number;
  standards: number;
  laboratories: number;
  by_source_type: StatsBySourceType[];
  by_licence: StatsByLicence[];
}

// ---- AI Assistant ----

export type ConfidenceState =
  | "VERIFIED"
  | "STRONG_EVIDENCE"
  | "NEEDS_CLARIFICATION"
  | "INSUFFICIENT_EVIDENCE"
  | "VERIFIED_EVIDENCE"
  | "PARTIAL_EVIDENCE"
  | "NO_VERIFIED_EVIDENCE";

export type GroundedEvidenceState =
  | "VERIFIED_EVIDENCE"
  | "PARTIAL_EVIDENCE"
  | "NO_VERIFIED_EVIDENCE";

export interface GroundedAnswerCitation {
  evidence_id: string;
  standard_number: string | null;
  clause_number: string | null;
  clause_title: string | null;
  document_id: string | null;
  source_id: string | null;
  title: string | null;
  version: string | number | null;
  page: number | null;
  url: string | null;
}

export interface GroundedAnswerEvidence {
  evidence_id: string;
  chunk_id: string;
  rank: number;
  standard: string | null;
  clause_number: string | null;
  clause_title: string | null;
  authoritative_text: string;
  context_prefix: string | null;
  document_id: string | null;
  source_id: string | null;
  title: string | null;
  version: string | number | null;
  page: number | null;
  url: string | null;
  retrieval_methods: ("semantic" | "bm25")[];
  semantic_rank: number | null;
  bm25_rank: number | null;
  rrf_score: number;
  reranker_score: number;
}

export interface GroundedAnswerResponse {
  query: string;
  answer: string;
  evidence_state: GroundedEvidenceState;
  citations: GroundedAnswerCitation[];
  limitations: string[];
  evidence: GroundedAnswerEvidence[];
  metadata: {
    provider: string | null;
    model: string | null;
    retrieval_ms: number | null;
    context_ms: number;
    generation_ms: number;
    evidence_count: number;
    evidence_state: GroundedEvidenceState;
    attempts: number;
    usage: {
      input_tokens: number | null;
      output_tokens: number | null;
      total_tokens: number | null;
    } | null;
  };
}

export interface AssistantCitation {
  /** Validated Stage 4.1 evidence identifier, for example E1. */
  evidence_id: string | null;
  source_id: string | null;
  document_id: string | null;
  title: string | null;
  version: string | number | null;
  clause: string | null;
  page: number | string | null;
  url: string | null;
  excerpt: string | null;
  standard_number: string | null;
  section: string | null;
  licence_class: string | null;
}

export interface AssistantSection {
  heading: string;
  content: string;
  clause_count: number;
}

export interface AssistantStandard {
  is_number: string;
  title: string | null;
  standard_id: string | null;
  citation_count: number;
}

export interface AssistantClauseFinding {
  clause: string | null;
  excerpt: string | null;
}

export interface AssistantAttribute {
  attribute: string;
  value: string;
}

export interface AssistantRouteStep {
  step: number;
  title: string;
  detail: string;
  actor: string;
}

export interface AssistantLaboratory {
  id: string;
  name: string;
  city: string | null;
  state: string | null;
  recognition_status: string;
  tests: string[];
}

export interface AssistantResponse {
  answer: string;
  sections: AssistantSection[];
  standards: AssistantStandard[];
  attributes: AssistantAttribute[];
  route: AssistantRouteStep[];
  laboratories: AssistantLaboratory[];
  certification: AssistantClauseFinding[];
  testing: AssistantClauseFinding[];
  next_steps: string[];
  citations: AssistantCitation[];
  confidence_state: ConfidenceState;
  clarification_question: string | null;
}

export interface AssistantQueryPayload {
  query: string;
  top_k?: number;
  filters?: RetrievalFilters;
  product_description?: string;
  standard_number?: string;
  source_type?: string;
  licence_class?: string;
}

// ---- Compliance Cases ----

export interface ComplianceCaseStandardRef {
  id: string;
  is_number: string;
  title: string;
}

export interface ComplianceCase {
  id: string;
  product: string;
  status: "open" | "in_progress" | "closed";
  created_at: string | null;
  standards: ComplianceCaseStandardRef[];
}

export interface ComplianceCaseCreated {
  id: string;
  product: string;
  status: string;
  standard_ids: string[];
  created_at: string | null;
}

export interface ComplianceCasesResponse {
  total: number;
  limit: number;
  offset: number;
  cases: ComplianceCase[];
}

// ---- Assistant stream events ----

export interface AssistantStageEvent {
  stage:
    | "understanding"
    | "standards"
    | "certification"
    | "testing"
    | "laboratories"
    | "evidence"
    | "error";
  message: string;
}

// ---- Existing (unchanged) ----

export interface ClauseNode {
  id: string;
  clause_path: string;
  title: string | null;
  depth: number;
  order_index: number;
  children: ClauseNode[];
}

export interface DocChunk {
  chunk_index: number;
  clause_node_id: string | null;
  clause_path: string | null;
  text: string | null;
}

export interface DocumentDetail {
  id: string;
  is_number: string | null;
  title: string;
  version: number;
  licence_class: string;
  full_text: string | null;
  source: SourceSummary;
  standard_id: string | null;
  standard: StandardSummary | null;
  clause_tree: ClauseNode[];
  chunks: DocChunk[];
}

export interface SourceTypeEntry {
  type: string;
  label: string;
}

export interface Health {
  status: string;
  service: string;
}

export interface HealthDb {
  status: string;
  service: string;
  database: string;
}
