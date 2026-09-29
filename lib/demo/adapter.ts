import { ApiError } from "../api/errors";
import {
  documents,
  documentDetail,
  laboratoryDetail,
  labs,
  sourceLabels,
  sources,
  standardDetail,
  standards,
  stats,
} from "./catalogue";
import { createCase, patchCase, readCases } from "./workspace";
import type { AssistantResponse, AssistantQueryPayload } from "../api/types";

export function demoAssistant(
  payload: AssistantQueryPayload,
): AssistantResponse {
  const q = (payload.product_description || payload.query).toLowerCase();
  const match = standards.find(
    (s) =>
      q.includes(s.is_number.split(":")[0].toLowerCase()) ||
      (s.sector === "Metallurgy" && /\bropes?\b/.test(q)) ||
      (s.sector === "Fire Safety" && /extinguisher/.test(q)) ||
      (s.sector === "Electrical Appliances" && /heater/.test(q)),
  );
  const empty: AssistantResponse = {
    answer: "",
    sections: [],
    standards: [],
    attributes: [],
    route: [],
    laboratories: [],
    certification: [],
    testing: [],
    next_steps: [],
    citations: [],
    confidence_state: "INSUFFICIENT_EVIDENCE",
    clarification_question: null,
  };
  if (!match)
    return {
      ...empty,
      answer:
        "This frontend demo has examples for steel wire ropes, portable fire extinguishers, and water heaters. There is no sample evidence for this product.",
      confidence_state:
        q.trim().split(/\s+/).length < 3
          ? "NEEDS_CLARIFICATION"
          : "INSUFFICIENT_EVIDENCE",
      clarification_question:
        "What is your product and its intended use? Try one of the sample products to explore a complete journey.",
      next_steps: [
        "Try a steel wire rope, fire extinguisher, or water heater.",
        "Browse the standards catalogue to inspect the available sample sources.",
      ],
    };
  const detail = standardDetail(match.id)!;
  const doc = detail.documents.find((d) => d.licence_class === "full_text_ok");
  const full = doc ? documentDetail(doc.id) : undefined;
  const citations = (full?.chunks || [])
    .slice(0, 4)
    .map((c, index) => ({
      evidence_id: `E${index + 1}`,
      source_id: full!.source.id,
      document_id: full!.id,
      title: full!.title,
      version: full!.version,
      clause: c.clause_path,
      page: null,
      url: `/sources/${full!.id}`,
      excerpt: c.text,
      standard_number: match.is_number,
      section: null,
      licence_class: full!.licence_class,
    }));
  return {
    ...empty,
    answer: `Demo match: ${match.is_number} — ${match.title}. Review the sample source clauses below, confirm the certification route, and choose a laboratory. This is a scripted preview, not a live compliance assessment.`,
    standards: [
      {
        standard_id: match.id,
        is_number: match.is_number,
        title: match.title,
        citation_count: citations.length,
      },
    ],
    attributes: [
      { attribute: "sector_hint", value: match.sector || "" },
      { attribute: "is_number", value: match.is_number },
    ],
    route: [
      {
        step: 1,
        title: "Confirm applicability",
        detail:
          "Review product scope and the applicable notification with the source document.",
        actor: "Product team",
      },
      {
        step: 2,
        title: "Prepare the application",
        detail:
          "Confirm the scheme, required records, and the correct application portal.",
        actor: "Compliance owner",
      },
      {
        step: 3,
        title: "Arrange testing",
        detail:
          "Choose a laboratory and confirm its current scope before sharing samples.",
        actor: "Laboratory + manufacturer",
      },
      {
        step: 4,
        title: "Record the outcome",
        detail:
          "Retain the assessment evidence and the application outcome for reference.",
        actor: "Compliance owner",
      },
    ],
    laboratories: detail.laboratories.map((l) => ({
      ...l,
      tests: laboratoryDetail(l.id)!.tests.map((t) => t.test_name),
    })),
    citations,
    confidence_state: "STRONG_EVIDENCE",
    testing:
      full?.chunks
        .filter((c) => /test|load|pressure/i.test(c.text || ""))
        .slice(0, 2)
        .map((c) => ({ clause: c.clause_path, excerpt: c.text })) || [],
    next_steps: [
      "Review the linked source and confirm applicability.",
      "Compare the listed laboratory capabilities against the required tests.",
    ],
  };
}

export async function demoRequest(
  path: string,
  options?: RequestInit,
): Promise<unknown> {
  const url = new URL(path, "http://demo.local");
  const p = url.pathname.replace("/api/v1", "").split("/").filter(Boolean);
  const q = url.searchParams;
  const body =
    typeof options?.body === "string" ? JSON.parse(options.body) : {};
  const page = <T>(rows: T[]) => {
    const limit = Math.max(1, Number(q.get("limit") || 20));
    const offset = Math.max(0, Number(q.get("offset") || 0));
    return {
      total: rows.length,
      limit,
      offset,
      results: rows.slice(offset, offset + limit),
    };
  };
  const has = (value: unknown, term: string | null) =>
    !term ||
    String(value || "")
      .toLowerCase()
      .includes(term.toLowerCase());
  const found = <T>(item: T | undefined) => {
    if (!item) throw new ApiError(404, "This item could not be found.");
    return item;
  };
  if (p[0] === "health")
    return { status: "ok", service: "frontend-demo", database: "demo" };
  if (p[0] === "stats") return stats;
  if (p[0] === "types")
    return {
      source_types: Object.entries(sourceLabels).map(([type, label]) => ({
        type,
        label,
      })),
      product_categories: [],
    };
  if (p[0] === "search")
    return page(
      documents.filter(
        (d) =>
          has(
            `${d.title} ${d.is_number} ${d.snippet} ${d.department}`,
            q.get("q"),
          ) &&
          has(d.source_type, q.get("source_type")) &&
          has(d.licence_class, q.get("licence")),
      ),
    );
  if (p[0] === "documents") return found(documentDetail(p[1]));
  if (p[0] === "standards") {
    if (!p[1]) {
      const rows = standards.filter(
        (s) =>
          has(`${s.title} ${s.is_number} ${s.description}`, q.get("q")) &&
          has(s.sector, q.get("sector")) &&
          has(s.status, q.get("status")) &&
          has(s.is_number, q.get("is_number")),
      );
      rows.sort(
        (a, b) =>
          String(q.get("sort") === "year" ? a.year : a.title).localeCompare(
            String(q.get("sort") === "year" ? b.year : b.title),
          ) * (q.get("order") === "desc" ? -1 : 1),
      );
      return page(rows);
    }
    const s = found(standardDetail(p[1]));
    if (p[2] === "documents")
      return { total: s.documents.length, documents: s.documents };
    if (p[2] === "amendments")
      return { standard_id: s.id, amendments: s.amendments };
    if (p[2] === "related") return { standard_id: s.id, related: s.related };
    if (p[2] === "laboratories")
      return { standard_id: s.id, laboratories: s.laboratories };
    return s;
  }
  if (p[0] === "laboratories") {
    if (p[1]) return found(laboratoryDetail(p[1]));
    return page(
      labs.filter((l) => {
        const detail = laboratoryDetail(l.id)!;
        return (
          has(l.name, q.get("q")) &&
          has(l.city, q.get("city")) &&
          has(l.state, q.get("state")) &&
          has(l.recognition_status, q.get("recognition_status")) &&
          (!q.get("standard_id") ||
            detail.standards.some((s) => s.id === q.get("standard_id"))) &&
          has(detail.tests.map((t) => t.test_name).join(" "), q.get("test"))
        );
      }),
    );
  }
  if (p[0] === "sources") {
    const result = page(
      sources.filter(
        (s) =>
          has(s.source_type, q.get("source_type")) &&
          has(s.licence_class, q.get("licence")),
      ),
    );
    return { ...result, sources: result.results };
  }
  if (p[0] === "assistant") return demoAssistant(body);
  if (p[0] === "compliance-cases") {
    if (options?.method === "POST") {
      const c = createCase(body);
      return { ...c, standard_ids: c.standards.map((s) => s.id) };
    }
    if (options?.method === "PATCH") return patchCase(p[1], body);
    if (p[1]) return found(readCases().find((c) => c.id === p[1]));
    const result = page(readCases());
    return { ...result, cases: result.results };
  }
  throw new Error(`Demo endpoint not implemented: ${url.pathname}`);
}
