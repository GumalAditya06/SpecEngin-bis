"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { CitationCard, CitationChip, uniqueCitations } from "@/components/Evidence";
import { DEMO_MODE } from "@/lib/api/config";
import type { AssistantCitation, AssistantResponse, AssistantStandard } from "@/lib/api";

function AnswerSection({ title, children }: { title: string; children: ReactNode }) {
  return <section className="research-section"><h2>{title}</h2>{children}</section>;
}

function CitedAnswerText({ text, citations, onSource }: { text: string; citations: AssistantCitation[]; onSource: (source: AssistantCitation) => void }) {
  // A token only becomes interactive when the validated backend citation list
  // contains the same evidence id. Unknown model-like text remains plain text.
  const available = new Map<string, AssistantCitation>();
  citations.forEach((citation) => {
    if (citation.evidence_id && !available.has(citation.evidence_id)) available.set(citation.evidence_id, citation);
  });
  return <>{text.split(/(\[E\d+\])/g).map((part, index) => {
    const match = /^\[(E\d+)\]$/.exec(part);
    const citation = match ? available.get(match[1]) : undefined;
    return citation
      ? <CitationChip key={`${part}-${index}`} citation={citation} onOpen={onSource} compact />
      : <span key={`${part}-${index}`}>{part}</span>;
  })}</>;
}

function AnswerCopy({ answer, citations, onSource }: { answer: string; citations: AssistantCitation[]; onSource: (source: AssistantCitation) => void }) {
  const paragraphs = answer.split(/\n\s*\n/).filter(Boolean);
  const long = answer.length > 750;
  if (!answer) return null;
  if (!long) return <p className="answer-prose"><CitedAnswerText text={answer} citations={citations} onSource={onSource} /></p>;
  return <>
    <p className="answer-prose"><CitedAnswerText text={paragraphs.length > 1 ? paragraphs[0] : "The response includes detailed findings. Review the full grounded answer and evidence below."} citations={citations} onSource={onSource} /></p>
    <details className="answer-detail"><summary>Read the full answer</summary><p className="answer-prose"><CitedAnswerText text={paragraphs.length > 1 ? paragraphs.slice(1).join("\n\n") : answer} citations={citations} onSource={onSource} /></p></details>
  </>;
}

export function StandardRecommendation({ standard, citations, onSource }: { standard: AssistantStandard; citations: AssistantCitation[]; onSource: (source: AssistantCitation) => void }) {
  const source = citations.find(c => c.standard_number === standard.is_number);
  return <article className="standard-recommendation">
    <p className="eyebrow">Relevant standard</p>
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3"><h3 className="font-mono text-lg">{standard.is_number}</h3><span className="pill text-muted">Relevance: review scope</span></div>
    <p className="mt-3 text-base">{standard.title || "Title unavailable"}</p>
    <div className="mt-5 border-t border-line pt-4"><p className="text-xs text-muted">Why this appears</p><p className="mt-2 text-sm leading-6 text-muted">{source ? `Linked to ${standard.citation_count} source reference${standard.citation_count === 1 ? "" : "s"} returned for your question. Read the scope to confirm applicability.` : "Returned as a candidate for your question. Supporting source evidence is not available in this response."}</p></div>
    <div className="mt-5 flex flex-wrap gap-3"><Link className="button" href={standard.standard_id ? `/standards/${standard.standard_id}` : `/standards?q=${encodeURIComponent(standard.is_number)}`}>View Standard <span aria-hidden>→</span></Link>{source && <button className="button" onClick={() => onSource(source)}>View Source <span aria-hidden>→</span></button>}</div>
  </article>;
}

export default function Answer({ result, onSource }: { result: AssistantResponse; onSource: (source: AssistantCitation) => void }) {
  const grounded = result.confidence_state === "VERIFIED_EVIDENCE" || result.confidence_state === "PARTIAL_EVIDENCE" || result.confidence_state === "NO_VERIFIED_EVIDENCE";
  const noVerifiedEvidence = result.confidence_state === "NO_VERIFIED_EVIDENCE";
  const insufficient = result.confidence_state === "INSUFFICIENT_EVIDENCE" || noVerifiedEvidence;
  const empty = !result.answer.trim() && !result.standards.length && !result.citations.length && !result.sections.length && !result.testing.length && !result.certification.length && !result.route.length && !result.laboratories.length;
  const labels = { VERIFIED: "Verified", STRONG_EVIDENCE: "Strong evidence", NEEDS_CLARIFICATION: "Needs clarification", INSUFFICIENT_EVIDENCE: "Insufficient evidence", VERIFIED_EVIDENCE: "Verified evidence", PARTIAL_EVIDENCE: "Partial evidence", NO_VERIFIED_EVIDENCE: "No verified evidence" };
  const citations = uniqueCitations(result.citations);
  return <div className="research-answer">
    <div className="answer-status"><span className="eyebrow">Research response</span><span className="pill" aria-label={`Evidence status: ${empty ? "No results" : labels[result.confidence_state]}`}>{empty ? "No results" : labels[result.confidence_state]}</span>{DEMO_MODE && <span className="text-xs text-muted">Sample response · scripted preview</span>}</div>
    <AnswerSection title="Answer">
      {noVerifiedEvidence && <p className="evidence-empty">No verified evidence</p>}
      {result.confidence_state === "INSUFFICIENT_EVIDENCE" && <p className="evidence-empty">I couldn&apos;t verify this from the available BIS sources.</p>}
      {empty && <p className="text-sm leading-7 text-muted">No results were returned. Try a product name, intended use or an IS number.</p>}
      {result.answer && <AnswerCopy answer={result.answer} citations={noVerifiedEvidence ? [] : citations} onSource={onSource} />}
      {result.confidence_state === "NEEDS_CLARIFICATION" && result.clarification_question && <div className="clarification"><p className="eyebrow">A little more context</p><p className="mt-3 text-sm leading-7">{result.clarification_question}</p></div>}
      {result.attributes.length > 0 && <ul className="mt-5 flex flex-wrap gap-2" aria-label="Product context">{result.attributes.map((a, i) => <li className="pill text-muted" key={i}>{a.value}</li>)}</ul>}
    </AnswerSection>
    {!grounded && result.standards.length > 0 && <AnswerSection title="Relevant standards"><div className="grid gap-4">{result.standards.map((standard, i) => <StandardRecommendation key={`${standard.is_number}-${i}`} standard={standard} citations={citations} onSource={onSource} />)}</div></AnswerSection>}
    {!grounded && result.sections.some(s => s.content.trim()) && <AnswerSection title="Requirements"><p className="mb-4 text-xs text-muted">Retrieved clauses · Review the original context before applying a requirement.</p>{result.sections.filter(s => s.content.trim()).map((s, i) => <details className="answer-detail" key={i} open={i === 0}><summary>{s.heading} <span className="text-muted">· {s.clause_count} clauses</span></summary><p className="answer-prose">{s.content}</p></details>)}</AnswerSection>}
    {!grounded && (result.route.length > 0 || result.certification.length > 0) && <AnswerSection title="Certification / BIS scheme"><ol className="certification-route">{result.route.map((s, i) => <li key={i}><span className="route-number">{s.step}</span><div><h3 className="text-sm font-medium">{s.title}</h3><p className="mt-2 text-sm leading-7 text-muted">{s.detail}</p>{s.actor && <p className="mt-2 text-xs text-muted">{s.actor}</p>}</div></li>)}</ol>{result.certification.map((c, i) => <p key={i} className="clause-finding">{c.clause && <span className="font-mono text-xs">§{c.clause} · </span>}{c.excerpt || "Excerpt unavailable. Review the original document."}</p>)}</AnswerSection>}
    {!grounded && result.testing.length > 0 && <AnswerSection title="Testing"><ul>{result.testing.map((t, i) => <li key={i} className="clause-finding">{t.clause && <span className="font-mono text-xs">§{t.clause} · </span>}{t.excerpt || "Excerpt unavailable. Review the original document."}</li>)}</ul></AnswerSection>}
    {!grounded && result.laboratories.length > 0 && <AnswerSection title="Laboratories"><div className="grid gap-3 sm:grid-cols-2">{result.laboratories.map(lab => <Link className="laboratory-recommendation" key={lab.id} href={`/laboratories/${lab.id}`}><span className="pill text-muted">{lab.recognition_status}</span><h3 className="mt-4 text-base">{lab.name}</h3><p className="mt-2 text-xs text-muted">{[lab.city, lab.state].filter(Boolean).join(", ")}</p>{lab.tests.length > 0 && <p className="mt-4 text-sm leading-6 text-muted">{lab.tests.join(" · ")}</p>}<span className="mt-5 block text-xs">View laboratory →</span></Link>)}</div></AnswerSection>}
    {!noVerifiedEvidence && citations.length > 0 && <AnswerSection title={grounded ? "Sources / Evidence" : "Sources"}><p className="mb-4 text-sm text-muted">Open a citation to inspect its validated document, location and source text.</p><div className="citation-grid">{grounded ? citations.map(citation => <CitationCard key={citation.evidence_id ?? `${citation.document_id}-${citation.clause}`} citation={citation} onOpen={onSource} />) : citations.map(citation => <CitationChip key={citation.evidence_id ?? `${citation.document_id}-${citation.clause}`} citation={citation} onOpen={onSource} />)}</div></AnswerSection>}
    {result.confidence_state === "PARTIAL_EVIDENCE" && result.next_steps.length > 0 && <AnswerSection title="Limitations"><ul className="limitations-list">{result.next_steps.map((limitation, i) => <li key={i}>{limitation}</li>)}</ul></AnswerSection>}
    {result.confidence_state !== "PARTIAL_EVIDENCE" && !grounded && result.next_steps.length > 0 && <details className="answer-detail next-steps"><summary>Further research</summary><ul className="mt-4 space-y-3 text-sm leading-6 text-muted">{result.next_steps.map((s, i) => <li key={i}>{s}</li>)}</ul></details>}
    {insufficient && !result.answer && !empty && <p className="sr-only">The available evidence did not verify an answer.</p>}
  </div>;
}
