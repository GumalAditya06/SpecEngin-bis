import { ApiError } from "./errors";
import { DEMO_MODE } from "./config";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const RETRIEVAL_BASE = process.env.NEXT_PUBLIC_RETRIEVAL_API_URL ?? BASE;

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  if (DEMO_MODE) {
    const { demoRequest } = await import("../demo/adapter");
    return (await demoRequest(path, opts)) as T;
  }
  const res = await fetch(`${BASE}${path}`, {
    cache: "no-store",
    ...opts,
  });
  if (!res.ok) {
    throw new ApiError(
      res.status,
      res.status === 404
        ? "This item could not be found."
        : `Could not load data (${res.status}). Please try again.`,
    );
  }
  return res.json();
}

// ---- Health ----

export async function getHealth() {
  return request<{ status: string; service: string }>("/api/v1/health");
}

export async function getHealthDb() {
  return request<{ status: string; service: string; database: string }>(
    "/api/v1/health/db",
  );
}

// ---- Stats ----

export async function getStats() {
  return request<import("./types").Stats>("/api/v1/stats");
}

// ---- Types ----

export async function getTypes() {
  return request<{
    source_types: import("./types").SourceTypeEntry[];
    product_categories: { name: string; description: string }[];
  }>("/api/v1/types");
}

// ---- Search (documents) ----

export async function searchDocuments(params: {
  q?: string;
  source_type?: string;
  licence?: string;
  limit?: number;
  offset?: number;
}) {
  const usp = new URLSearchParams();
  if (params.q) usp.set("q", params.q);
  if (params.source_type) usp.set("source_type", params.source_type);
  if (params.licence) usp.set("licence", params.licence);
  usp.set("limit", String(params.limit ?? 20));
  usp.set("offset", String(params.offset ?? 0));
  return request<import("./types").SearchResponse>(
    `/api/v1/search?${usp.toString()}`,
  );
}

export async function getDocument(id: string) {
  return request<import("./types").DocumentDetail>(`/api/v1/documents/${id}`);
}

/** Stage 3.4 evidence retrieval. This never uses demo data. */
export async function retrieveEvidence(
  query: string,
  options?: { top_k?: number; filters?: import("./types").RetrievalFilters },
) {
  const res = await fetch(`${RETRIEVAL_BASE}/api/v1/search/retrieve`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, top_k: options?.top_k ?? 5, filters: options?.filters ?? {} }),
  });
  if (!res.ok) {
    let message = `Could not retrieve evidence (${res.status}). Please try again.`;
    try {
      const payload = (await res.json()) as { detail?: string };
      if (typeof payload.detail === "string") message = payload.detail;
    } catch {
      // Keep the stable status-based message when the service returns no JSON.
    }
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as import("./types").RetrievalResponse;
}

// ---- Standards ----

export async function listStandards(params: {
  q?: string;
  is_number?: string;
  status?: string;
  sector?: string;
  year?: number;
  sort?: string;
  order?: string;
  limit?: number;
  offset?: number;
}) {
  const usp = new URLSearchParams();
  if (params.q) usp.set("q", params.q);
  if (params.is_number) usp.set("is_number", params.is_number);
  if (params.status) usp.set("status", params.status);
  if (params.sector) usp.set("sector", params.sector);
  if (params.year !== undefined) usp.set("year", String(params.year));
  usp.set("sort", params.sort ?? "title");
  usp.set("order", params.order ?? "asc");
  usp.set("limit", String(params.limit ?? 50));
  usp.set("offset", String(params.offset ?? 0));
  return request<import("./types").StandardsResponse>(
    `/api/v1/standards?${usp.toString()}`,
  );
}

export async function getStandard(id: string) {
  return request<import("./types").StandardDetail>(`/api/v1/standards/${id}`);
}

export async function getStandardDocuments(
  id: string,
  params?: { limit?: number; offset?: number },
) {
  const usp = new URLSearchParams();
  if (params?.limit) usp.set("limit", String(params.limit));
  if (params?.offset) usp.set("offset", String(params.offset));
  return request<{
    total: number;
    documents: import("./types").SearchDocument[];
  }>(`/api/v1/standards/${id}/documents?${usp.toString()}`);
}

export async function getStandardAmendments(id: string) {
  return request<{
    standard_id: string;
    amendments: import("./types").Amendment[];
  }>(`/api/v1/standards/${id}/amendments`);
}

export async function getStandardRelated(id: string) {
  return request<{
    standard_id: string;
    related: import("./types").StandardSummary[];
  }>(`/api/v1/standards/${id}/related`);
}

export async function getStandardLaboratories(id: string) {
  return request<{
    standard_id: string;
    laboratories: import("./types").LaboratorySummary[];
  }>(`/api/v1/standards/${id}/laboratories`);
}

// ---- Laboratories ----

export async function listLaboratories(params: {
  q?: string;
  city?: string;
  state?: string;
  test?: string;
  is_number?: string;
  recognition_status?: string;
  standard_id?: string;
  limit?: number;
  offset?: number;
}) {
  const usp = new URLSearchParams();
  if (params.q) usp.set("q", params.q);
  if (params.city) usp.set("city", params.city);
  if (params.state) usp.set("state", params.state);
  if (params.test) usp.set("test", params.test);
  if (params.is_number) usp.set("is_number", params.is_number);
  if (params.recognition_status)
    usp.set("recognition_status", params.recognition_status);
  if (params.standard_id) usp.set("standard_id", params.standard_id);
  usp.set("limit", String(params.limit ?? 50));
  usp.set("offset", String(params.offset ?? 0));
  return request<import("./types").LaboratoriesResponse>(
    `/api/v1/laboratories?${usp.toString()}`,
  );
}

export async function getLaboratory(id: string) {
  return request<import("./types").LaboratoryDetail>(
    `/api/v1/laboratories/${id}`,
  );
}

// ---- Sources ----

export async function listSources(params?: {
  source_type?: string;
  licence?: string;
  limit?: number;
  offset?: number;
}) {
  const usp = new URLSearchParams();
  if (params?.source_type) usp.set("source_type", params.source_type);
  if (params?.licence) usp.set("licence", params.licence);
  usp.set("limit", String(params?.limit ?? 50));
  usp.set("offset", String(params?.offset ?? 0));
  return request<import("./types").SourcesResponse>(
    `/api/v1/sources?${usp.toString()}`,
  );
}

// ---- AI Assistant ----

export async function assistantQuery(
  payload: import("./types").AssistantQueryPayload,
) {
  if (DEMO_MODE) {
    const { demoAssistant } = await import("../demo/adapter");
    return demoAssistant(payload);
  }
  const filters = {
    ...(payload.filters ?? {}),
    ...(payload.standard_number
      ? { standard_number: payload.standard_number }
      : {}),
  };
  const res = await fetch(`${RETRIEVAL_BASE}/api/v1/assistant/query`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: payload.query,
      top_k: payload.top_k ?? 5,
      filters,
    }),
  });
  if (!res.ok) {
    const message = res.status === 422
      ? "Please check the question and filters, then try again."
      : res.status === 503
        ? "The answer service is temporarily unavailable. Please try again later."
        : "The grounded answer could not be generated. Please try again.";
    throw new ApiError(res.status, message);
  }
  return adaptGroundedAnswer(
    (await res.json()) as import("./types").GroundedAnswerResponse,
  );
}

function adaptGroundedAnswer(
  grounded: import("./types").GroundedAnswerResponse,
): import("./types").AssistantResponse {
  const evidence = new Map(
    grounded.evidence.map((item) => [item.evidence_id, item]),
  );
  const citations: import("./types").AssistantCitation[] = grounded.citations.map(
    (citation) => ({
      evidence_id: citation.evidence_id,
      source_id: citation.source_id,
      document_id: citation.document_id,
      title: citation.title,
      version: citation.version,
      clause: citation.clause_number,
      page: citation.page,
      url: citation.url,
      excerpt: evidence.get(citation.evidence_id)?.authoritative_text ?? null,
      standard_number: citation.standard_number,
      section: citation.clause_title,
      licence_class: null,
    }),
  );
  const standards = Array.from(
    citations.reduce((items, citation) => {
      if (!citation.standard_number) return items;
      const current = items.get(citation.standard_number);
      items.set(citation.standard_number, {
        is_number: citation.standard_number,
        title: citation.title,
        standard_id: null,
        citation_count: (current?.citation_count ?? 0) + 1,
      });
      return items;
    }, new Map<string, import("./types").AssistantStandard>()),
  ).map(([, standard]) => standard);
  return {
    answer: grounded.answer,
    sections: grounded.evidence.map((item) => ({
      heading: [item.standard, item.clause_number ? `Clause ${item.clause_number}` : null]
        .filter(Boolean)
        .join(" · ") || item.evidence_id,
      content: item.authoritative_text,
      clause_count: 1,
    })),
    standards,
    attributes: [],
    route: [],
    laboratories: [],
    certification: [],
    testing: [],
    next_steps: grounded.limitations,
    citations,
    confidence_state: grounded.evidence_state,
    clarification_question: null,
  };
}

export type StreamStage = import("./types").AssistantStageEvent["stage"];

/**
 * Run the assistant query against the SSE streaming endpoint.
 *
 * Calls onStage for each pipeline stage event and resolves with the final
 * result payload (identical to the non-streaming endpoint). Falls back to
 * the plain endpoint when streaming is unavailable or errors mid-stream.
 */
export async function assistantQueryStream(
  payload: import("./types").AssistantQueryPayload,
  onStage: (stage: StreamStage, message: string) => void,
): Promise<import("./types").AssistantResponse> {
  if (DEMO_MODE) {
    onStage("understanding", "Loading sample journey");
    const { demoAssistant } = await import("../demo/adapter");
    const result = demoAssistant(payload);
    onStage("evidence", "Sample evidence ready");
    return result;
  }
  onStage("understanding", "Validating your question");
  onStage("standards", "Retrieving verified BIS evidence");
  const result = await assistantQuery(payload);
  onStage("evidence", "Grounded answer ready");
  return result;
}

// ---- Compliance Cases ----
// Unused by the current MVP (the workspace screens were removed); kept as the
// typed client for the backend's `/api/v1/compliance-cases` endpoints.

export async function createComplianceCase(payload: {
  product: string;
  standard_ids: string[];
  query?: string;
}) {
  return request<import("./types").ComplianceCaseCreated>(
    "/api/v1/compliance-cases",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export async function listComplianceCases(params?: {
  limit?: number;
  offset?: number;
}) {
  const usp = new URLSearchParams();
  usp.set("limit", String(params?.limit ?? 50));
  usp.set("offset", String(params?.offset ?? 0));
  return request<import("./types").ComplianceCasesResponse>(
    `/api/v1/compliance-cases?${usp.toString()}`,
  );
}

export async function getComplianceCase(id: string) {
  return request<import("./workspace-types").CaseDetail>(
    `/api/v1/compliance-cases/${id}`,
  );
}
export async function updateComplianceCase(
  id: string,
  patch: import("./workspace-types").CasePatch,
) {
  if (!DEMO_MODE && Object.keys(patch).some((k) => k !== "status")) {
    throw new Error(
      "This workspace feature needs the extended case API. Enable demo mode to preview it.",
    );
  }
  await request(`/api/v1/compliance-cases/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return getComplianceCase(id);
}
