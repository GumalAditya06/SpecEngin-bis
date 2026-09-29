"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useMemo, useState } from "react";

import SourceCard from "@/components/sources/SourceCard";
import SourceDrawer, { type SourceDrawerContext } from "@/components/sources/SourceDrawer";
import type { Amendment, DocumentDetail, DocChunk, LaboratorySummary, SearchDocument, StandardDetail, StandardSummary } from "@/lib/api";
import { toSourceRecord, toSourceRecordFromDetail } from "@/lib/sources/records";
import type { SourceRecord } from "@/lib/sources/types";

type ClauseFinding = { id: string; document: DocumentDetail; chunk: DocChunk; title: string | null };
type OpenSource = { source: SourceRecord; clause: string | null; section: string | null; excerpt: string | null };

const sectionKeywords = {
  scope: ["scope", "covers", "applicable", "application"],
  requirements: ["requirement", "material", "construction", "quality", "dimensions", "breaking", "load", "shall"],
  testing: ["test", "testing", "proof", "tensile", "discharge", "freezing"],
  marking: ["mark", "marking", "label", "labelling", "manufacturer"],
};

function titleCaseStatus(status: string) { return status ? status.charAt(0).toUpperCase() + status.slice(1) : "Current"; }
function findClauseTitle(document: DocumentDetail, chunk: DocChunk) { return document.clause_tree.find((node) => node.clause_path === chunk.clause_path)?.title || null; }
function sourceFor(document: DocumentDetail, chunk?: DocChunk | null): OpenSource {
  return { source: toSourceRecordFromDetail(document), clause: chunk?.clause_path ?? null, section: chunk ? findClauseTitle(document, chunk) : (document.source?.source_type_label ?? null), excerpt: chunk?.text || document.full_text?.slice(0, 420) || null };
}
function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) { return <section id={id} className="standard-editorial-section"><h2>{title}</h2><div className="standard-section-body">{children}</div></section>; }
function ClauseButton({ finding, onOpen }: { finding: ClauseFinding; onOpen: (source: SourceRecord, context: SourceDrawerContext) => void }) {
  const label = finding.chunk.clause_path ? `Clause ${finding.chunk.clause_path}` : "Source excerpt";
  return <details className="standard-clause"><summary><span className="font-mono text-xs text-chroma">{label}</span><span>{finding.title || "Extracted content"}</span></summary><button type="button" className="mt-3 block w-full text-left" onClick={() => onOpen(toSourceRecordFromDetail(finding.document), { clause: finding.chunk.clause_path, section: finding.title, excerpt: finding.chunk.text })}><p className="text-sm leading-7 text-muted">{finding.chunk.text || "Clause metadata only; open the source document."}</p><span className="mt-4 inline-flex text-xs text-ink">Open source viewer →</span></button></details>;
}
function pickFindings(findings: ClauseFinding[], keywords: string[], limit = 3) { return findings.filter((finding) => { const haystack = `${finding.title || ""} ${finding.chunk.text || ""}`.toLowerCase(); return keywords.some((keyword) => haystack.includes(keyword)); }).slice(0, limit); }

export default function StandardsDetail({ standard, documents, documentDetails, documentsTotal, amendments, related, laboratories }: { standard: StandardDetail; documents: SearchDocument[]; documentDetails: DocumentDetail[]; documentsTotal: number; amendments: Amendment[]; related: StandardSummary[]; laboratories: LaboratorySummary[] }) {
  const [open, setOpen] = useState<OpenSource | null>(null);
  const findings = useMemo(() => documentDetails.flatMap((document) => document.chunks.filter((chunk) => chunk.clause_path || chunk.text).map((chunk) => ({ id: `${document.id}-${chunk.chunk_index}`, document, chunk, title: findClauseTitle(document, chunk) }))), [documentDetails]);
  const scope = pickFindings(findings, sectionKeywords.scope, 2);
  const requirements = pickFindings(findings, sectionKeywords.requirements, 4);
  const testing = pickFindings(findings, sectionKeywords.testing, 4);
  const marking = pickFindings(findings, sectionKeywords.marking, 3);
  const fallbackClauses = findings.slice(0, 5);
  const sources = useMemo(() => documents.map((document) => { const detail = documentDetails.find((item) => item.id === document.id); return detail ? toSourceRecordFromDetail(detail) : toSourceRecord(document); }), [documents, documentDetails]);
  function openSource(source: SourceRecord, context: SourceDrawerContext) { setOpen({ source, clause: context.clause ?? null, section: context.section ?? null, excerpt: context.excerpt ?? null }); }
  function openFromList(source: SourceRecord) { const detail = documentDetails.find((item) => item.id === source.id); setOpen(detail ? sourceFor(detail) : { source, clause: null, section: null, excerpt: source.description }); }

  return <section className="page-wrap standard-detail">
    <Link href="/standards" className="text-sm text-muted transition-colors hover:text-ink">← All standards</Link>
    <header className="standard-detail-hero"><div><p className="font-mono text-sm text-chroma">{standard.is_number}</p><h1>{standard.title}</h1><div className="mt-5 flex flex-wrap gap-2"><span className="pill text-chroma">{titleCaseStatus(standard.status)}</span>{standard.sector && <span className="pill">{standard.sector}</span>}{standard.year && <span className="pill">{standard.year}</span>}</div></div><div className="standard-detail-actions"><Link className="button button-primary" href={`/assistant?q=${encodeURIComponent(`Tell me about ${standard.is_number}`)}`}>Ask Assistant about this standard →</Link><Link className="button" href={`/laboratories?standard_id=${standard.id}`}>Find laboratories →</Link></div></header>
    <nav className="standard-section-nav" aria-label="Standard sections">{[["overview", "Overview"], ["scope", "Scope"], ["requirements", "Requirements"], ["testing", "Testing"], ["marking", "Marking"], ["related", "Related standards"], ["sources", "Sources"]].map(([href, label]) => <a key={href} href={`#${href}`}>{label}</a>)}</nav>
    <div className="standard-editorial">
      <Section id="overview" title="Overview"><p className="standard-lede">{standard.description || "Review the linked BIS source documents to confirm product scope, evidence and the applicable next step."}</p><dl className="standard-facts"><div><dt>Sector</dt><dd>{standard.sector || "Not recorded"}</dd></div><div><dt>Year</dt><dd>{standard.year || "Not recorded"}</dd></div><div><dt>Status</dt><dd>{titleCaseStatus(standard.status)}</dd></div><div><dt>Sources</dt><dd>{documentsTotal} document{documentsTotal === 1 ? "" : "s"}</dd></div></dl>{standard.certification_schemes.length > 0 && <div className="mt-6"><p className="eyebrow">BIS scheme</p><ul className="mt-3 grid gap-3">{standard.certification_schemes.map((scheme) => <li key={scheme.id} className="standard-inline-card">{scheme.title}</li>)}</ul></div>}</Section>
      <Section id="scope" title="Scope">{scope.length ? scope.map((finding) => <ClauseButton key={finding.id} finding={finding} onOpen={openSource} />) : <p className="text-sm leading-7 text-muted">Scope-level clause text is not indexed for this standard yet. Open the source documents to verify applicability.</p>}</Section>
      <Section id="requirements" title="Requirements">{(requirements.length ? requirements : fallbackClauses).map((finding) => <ClauseButton key={finding.id} finding={finding} onOpen={openSource} />)}{!findings.length && <p className="text-sm leading-7 text-muted">Clause-level requirements are not available in this catalogue entry.</p>}</Section>
      <Section id="testing" title="Testing">{testing.length ? testing.map((finding) => <ClauseButton key={finding.id} finding={finding} onOpen={openSource} />) : <p className="text-sm leading-7 text-muted">Testing clauses were not separately extracted. Use the sources and associated laboratories to continue the review.</p>}{laboratories.length > 0 && <div className="mt-6 grid gap-3">{laboratories.map((lab) => <Link key={lab.id} href={`/laboratories/${lab.id}`} className="standard-inline-card transition-colors hover:border-line-strong"><span>{lab.name}</span><span className="text-faint">{[lab.city, lab.state].filter(Boolean).join(", ")}{lab.recognition_status ? ` · ${lab.recognition_status}` : ""}</span></Link>)}</div>}</Section>
      <Section id="marking" title="Marking">{marking.length ? marking.map((finding) => <ClauseButton key={finding.id} finding={finding} onOpen={openSource} />) : <p className="text-sm leading-7 text-muted">Marking clauses are not separately identified in the indexed text. Check the source documents before making a compliance decision.</p>}</Section>
      <Section id="related" title="Related standards">{related.length ? <ul className="grid gap-3">{related.map((item) => <li key={item.id}><Link href={`/standards/${item.id}`} className="standard-inline-card transition-colors hover:border-line-strong"><span className="font-mono text-xs text-chroma">{item.is_number}</span><span>{item.title}</span></Link></li>)}</ul> : <p className="text-sm leading-7 text-muted">No related standards are linked in this catalogue entry.</p>}</Section>
      <Section id="sources" title="Sources">{sources.length ? <div className="grid gap-3">{sources.map((source) => <SourceCard key={source.id} source={source} action="drawer" onOpen={openFromList} />)}</div> : <p className="text-sm leading-7 text-muted">No source documents are associated with this standard yet.</p>}</Section>
      {amendments.length > 0 && <Section id="amendments" title="Amendments"><ul className="grid gap-3">{amendments.map((amendment) => <li key={amendment.id} className="standard-inline-card"><span className="font-mono text-xs text-chroma">Amendment {amendment.amendment_number}</span><span>{amendment.title || "Untitled amendment"}</span>{amendment.effective_date && <span className="text-faint">Effective {new Date(amendment.effective_date).toLocaleDateString("en-IN")}</span>}</li>)}</ul></Section>}
    </div>
    <SourceDrawer source={open?.source ?? null} clause={open?.clause} section={open?.section} excerpt={open?.excerpt} onClose={() => setOpen(null)} />
  </section>;
}
