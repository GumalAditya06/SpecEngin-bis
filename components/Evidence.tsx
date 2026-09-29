"use client";

import Link from "next/link";
import type { AssistantCitation } from "@/lib/api";

export function citationKey(citation: AssistantCitation) {
  return citation.evidence_id ?? [
    citation.document_id,
    citation.clause,
    citation.page,
    citation.excerpt,
  ].map((value) => value ?? "").join("|");
}

/** Preserve backend citation order while avoiding repeated evidence cards. */
export function uniqueCitations(citations: AssistantCitation[]) {
  const seen = new Set<string>();
  return citations.filter((citation) => {
    const key = citationKey(citation);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function citationLabel(citation: AssistantCitation) {
  return citation.evidence_id ? `[${citation.evidence_id}]` : "Source";
}

/** Keyboard-accessible reference used both inline and in compact previews. */
export function CitationChip({
  citation,
  onOpen,
  compact = false,
}: {
  citation: AssistantCitation;
  onOpen: (citation: AssistantCitation) => void;
  compact?: boolean;
}) {
  const location = [
    citation.standard_number,
    citation.clause ? `Clause ${citation.clause}` : null,
    citation.page != null ? `page ${citation.page}` : null,
  ].filter(Boolean).join(", ");
  return (
    <button
      type="button"
      onClick={() => onOpen(citation)}
      className={compact
        ? "citation-reference"
        : "inline-flex max-w-full items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-left text-[11px] text-muted transition-colors hover:border-line-strong hover:text-ink focus-visible:ring-2 focus-visible:ring-white/40 focus-visible:outline-none"}
      aria-label={`View evidence ${citationLabel(citation)}${location ? `: ${location}` : ""}`}
      title={citation.excerpt ?? citation.title ?? "Source evidence"}
    >
      {!compact && <span className="h-1 w-1 shrink-0 rounded-full bg-chroma" aria-hidden />}
      <span className="shrink-0 font-mono">{citationLabel(citation)}</span>
      {!compact && citation.standard_number && <span className="shrink-0 font-mono">{citation.standard_number}</span>}
      {!compact && citation.clause && <span className="shrink-0">§{citation.clause}</span>}
      {!compact && citation.page != null && <span className="shrink-0 text-faint">p.{citation.page}</span>}
    </button>
  );
}

/** Reusable grounded-evidence summary; the drawer remains the full viewer. */
export function CitationCard({ citation, onOpen }: { citation: AssistantCitation; onOpen: (citation: AssistantCitation) => void }) {
  const metadata = [
    ["Standard", citation.standard_number],
    ["Document", citation.title],
    ["Clause", citation.clause],
    ["Section", citation.section],
    ["Page", citation.page != null ? String(citation.page) : null],
    ["Version", citation.version != null ? String(citation.version) : null],
  ].filter((row): row is [string, string] => Boolean(row[1]));
  const headingId = `citation-${citation.evidence_id ?? citationKey(citation)}`;
  return (
    <article className="citation-card" aria-labelledby={headingId}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 id={headingId} className="font-mono text-xs text-chroma">{citationLabel(citation)}</h3>
        {citation.standard_number && <span className="pill font-mono">{citation.standard_number}</span>}
      </div>
      {metadata.length > 0 && <dl className="citation-metadata">
        {metadata.map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}
      </dl>}
      {citation.excerpt && <p className="citation-excerpt">{citation.excerpt}</p>}
      <div className="mt-5 flex flex-wrap gap-3">
        <button type="button" className="button" onClick={() => onOpen(citation)}>View source <span aria-hidden>→</span></button>
        {citation.document_id && <Link className="button" href={`/sources/${encodeURIComponent(citation.document_id)}`}>Open document <span aria-hidden>→</span></Link>}
      </div>
    </article>
  );
}
