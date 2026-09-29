"use client";

import { useState } from "react";
import type { RetrievalEvidence, SearchDocument } from "@/lib/api";
import { trustedSourceUrl } from "@/lib/sources/url";

function LicenceBadge({ licence_class }: { licence_class: string }) {
  const full = licence_class === "full_text_ok";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] ${
        full ? "border-line-strong text-ink" : "border-line text-faint"
      }`}
    >
      <span
        aria-hidden
        className={`h-1.5 w-1.5 rounded-full ${full ? "bg-chroma" : "bg-faint"}`}
      />
      {full ? "Full text" : "Metadata only"}
    </span>
  );
}

export function SearchResults({
  results,
  onSelect,
}: {
  results: SearchDocument[];
  onSelect: (id: string) => void;
}) {
  if (results.length === 0) return null;
  return (
    <ul className="result-list mt-4 space-y-3">
      {results.map((doc) => (
        <li key={doc.id} className="result-reveal">
          <button
            type="button"
            onClick={() => onSelect(doc.id)}
            aria-label={`Open ${doc.title}`}
            className="interactive-card w-full rounded-[var(--r-card)] border border-line bg-surface p-6 text-left hover:border-line-strong hover:bg-elevated focus-visible:ring-2 focus-visible:ring-white/40 focus-visible:outline-none"
          >
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              {doc.is_number && (
                <span className="font-mono text-[0.8125rem] text-ink">
                  {doc.is_number}
                </span>
              )}
              <LicenceBadge licence_class={doc.licence_class} />
              <span className="text-[11px] text-faint">{doc.source_type_label}</span>
            </div>
            <h3 className="mt-2 text-lg font-medium leading-7 tracking-tight text-ink">
              {doc.title}
            </h3>
            {doc.snippet ? (
              <p className="mt-2 text-sm leading-6 text-muted">{doc.snippet}</p>
            ) : (
              <p className="mt-2 text-sm leading-6 text-faint">
                Full text is not stored for this document under its licence class.
              </p>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-faint">
              <span>{doc.publisher}</span>
              <span>
                {doc.clause_count} clause{doc.clause_count === 1 ? "" : "s"}
              </span>
              {doc.sector && <span>{doc.sector}</span>}
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}

export function ResultEmptyState({ query }: { query: string }) {
  return (
    <div className="rounded-[var(--r-card)] border border-line bg-surface p-12 text-center">
      <p className="text-lg font-medium text-ink">Nothing matched that query.</p>
      <p className="mx-auto mt-2 max-w-[48ch] text-sm text-muted">
        Try a product, an IS number like&nbsp;8921, or a department like
        &ldquo;Ministry of Steel&rdquo;
        {query ? ` (you searched “${query}”)` : ""}.
      </p>
    </div>
  );
}

function matchReason(result: RetrievalEvidence) {
  const methods = result.retrieval.methods;
  if (methods.includes("semantic") && methods.includes("bm25")) return "Matched both the meaning of your query and its exact terminology.";
  if (methods.includes("bm25")) return "Matched exact terminology in this source passage.";
  return "Matched the meaning of your query in this source passage.";
}

export function EvidenceSearchResults({ results }: { results: RetrievalEvidence[] }) {
  const [openId, setOpenId] = useState<string | null>(null);
  return <ol className="result-list space-y-3">{results.map((result) => {
    const open = openId === result.chunk_id;
    const sourceUrl = trustedSourceUrl(result.source.url);
    const clause = result.clause_number ? `Clause ${result.clause_number}${result.clause_title ? ` — ${result.clause_title}` : ""}` : result.clause_title || "Clause not recorded";
    return <li key={result.chunk_id} className="result-reveal rounded-[var(--r-card)] border border-line bg-surface">
      <button type="button" className="interactive-card w-full p-6 text-left hover:bg-elevated focus-visible:ring-2 focus-visible:ring-white/40 focus-visible:outline-none" aria-expanded={open} onClick={() => setOpenId(open ? null : result.chunk_id)}>
        <div className="flex flex-wrap items-center justify-between gap-3"><span className="font-mono text-[0.8125rem] text-chroma">{result.standard_number || "Standard not recorded"}</span><span className="pill">Evidence {result.rank}</span></div>
        <h2 className="mt-3 text-lg font-medium leading-7 tracking-tight text-ink">{clause}</h2>
        {result.source.title && <p className="mt-1 text-xs text-faint">{result.source.title}</p>}
        <p className="mt-3 line-clamp-4 whitespace-pre-wrap text-sm leading-6 text-muted">{result.text}</p>
        <div className="mt-4 flex items-center justify-between gap-4 text-xs"><span className="text-faint">{matchReason(result)}</span><span className="shrink-0 text-ink">{open ? "Hide evidence ↑" : "Inspect evidence →"}</span></div>
      </button>
      {open && <section className="border-t border-line p-6" aria-label={`Provenance for result ${result.rank}`}>
        <h3 className="text-[0.8125rem] font-medium text-ink">Retrieved evidence</h3>
        <p className="mt-3 whitespace-pre-wrap rounded-[var(--r-inner)] border border-line bg-page px-4 py-3 text-sm leading-7 text-muted">{result.text}</p>
        <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
          <div><dt className="text-xs text-faint">Standard</dt><dd className="mt-1 font-mono">{result.standard_number || "Not recorded"}</dd></div>
          <div><dt className="text-xs text-faint">Clause</dt><dd className="mt-1">{clause}</dd></div>
          <div><dt className="text-xs text-faint">Document</dt><dd className="mt-1">{result.source.title || result.source.document_id || "Not recorded"}</dd></div>
          <div><dt className="text-xs text-faint">Version</dt><dd className="mt-1">{result.source.version ?? "Not recorded"}</dd></div>
          <div><dt className="text-xs text-faint">Page</dt><dd className="mt-1">{result.source.page ?? "Not recorded"}</dd></div>
          <div><dt className="text-xs text-faint">Why this appeared</dt><dd className="mt-1">{matchReason(result)}</dd></div>
        </dl>
        {sourceUrl && <a className="button mt-5" href={sourceUrl} target="_blank" rel="noopener noreferrer">Open authoritative source ↗</a>}
      </section>}
    </li>;
  })}</ol>;
}
