"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { Logo } from "@/components/Logo";
import { CitationChip } from "@/components/Evidence";
import SourceDrawer from "@/components/sources/SourceDrawer";
import { sourceFromCitation } from "@/lib/sources/records";
import type { AssistantCitation } from "@/lib/api";

export const sampleQuestion = "I manufacture stainless steel pressure cookers. Which BIS standard applies to my product?";
const citation: AssistantCitation = {
  evidence_id: "E1", version: null,
  source_id: null, document_id: null, title: "Illustrative source · Product requirements",
  standard_number: "IS XXXX:2024", clause: "4.2", page: 8, section: "Requirements",
  url: null, licence_class: "full_text_ok",
  excerpt: "This is an illustrative citation, not an extract from an Indian Standard. In the Assistant, this panel displays available source text, clause references, and a link to the original document. Check the applicable standard before making a compliance decision.",
};

/**
 * The landing illustration opens the same SourceDrawer the product uses. The
 * citation has no document id, so the drawer deliberately shows no
 * navigation actions — the example cannot pretend to open a real source.
 */
export function EvidenceExample() {
  const [open, setOpen] = useState(false);
  return <><CitationChip citation={citation} onOpen={() => setOpen(true)} /><SourceDrawer source={open ? sourceFromCitation(citation) : null} clause={citation.clause} page={citation.page} section={citation.section} excerpt={citation.excerpt} onClose={() => setOpen(false)} /></>;
}

export function PreviewFrame({ title, children, className = "" }: { title: string; children: ReactNode; className?: string }) {
  return <div className={`product-preview ${className}`}>
    <div className="preview-bar"><span className="flex items-center gap-3"><Logo variant="mark" title="" className="h-5 w-5" />{title}</span><span className="preview-label">Illustrative preview</span></div>
    <div className="preview-body">{children}</div>
  </div>;
}

export function AssistantPreview({ stage = 1, compact = false }: { stage?: number; compact?: boolean }) {
  return <PreviewFrame title="Compliance assistant">
    <div className="preview-query"><span className="eyebrow">Your question</span><p>{sampleQuestion}</p></div>
    <div className="preview-content" key={stage}>
      {stage === 0 ? <div className="preview-block"><p className="text-sm text-ink">Product understanding</p><div className="mt-4 flex flex-wrap gap-2"><span className="pill">Stainless steel</span><span className="pill">Pressure cooker</span><span className="pill">Intended use</span></div><p className="preview-copy">What is the cooker’s capacity and construction? Product details help narrow the scope.</p></div> : null}
      {stage === 1 ? <div className="preview-block"><div className="flex flex-wrap justify-between gap-3"><p className="text-sm">Candidate standards</p><span className="pill text-muted">Scope review needed</span></div><div className="mt-4 border-l border-line-strong pl-4"><span className="font-mono text-xs text-chroma">IS XXXX:2024 · Example reference</span><p className="mt-2 text-lg">Product specification and scope</p></div><p className="preview-copy">Confirm construction, material and intended use before selecting an Indian Standard.</p><div className="mt-4 flex flex-wrap gap-2"><span className="pill">Scope</span><span className="pill">Requirements</span><span className="pill">Sources</span></div></div> : null}
      {stage === 2 ? <div className="preview-block"><p className="text-sm">Requirements to review</p>{["Material and construction", "Performance and testing", "Marking and labelling"].map((text, i) => <div className={`requirement-row ${i === 1 ? "requirement-selected" : ""}`} key={text}><span className="text-chroma">0{i + 1}</span>{text}</div>)}</div> : null}
      {stage === 3 ? <div className="preview-block"><p className="text-sm">Evidence behind the answer</p><p className="preview-copy">Open a citation to inspect its clause, page and available source excerpt.</p><div className="mt-5"><EvidenceExample /></div><p className="mt-4 text-xs text-muted">Example reference · Click to open the source panel</p></div> : null}
      {stage === 4 ? <div className="preview-block"><p className="text-sm">Testing and laboratories</p><div className="mt-4 rounded-[var(--r-inner)] border border-line p-4"><p className="text-base">Materials testing laboratory</p><p className="mt-2 text-xs text-muted">Example listing · Material and performance tests</p><span className="pill mt-3">Recognition details →</span></div><div className="mt-4 flex flex-wrap gap-2"><span className="pill">Standard</span><span className="pill">Testing capability</span><span className="pill">Location</span></div><p className="preview-copy">Review listed capabilities and recognition details before contacting a laboratory.</p></div> : null}
    </div>
    {!compact && <div className="preview-composer"><span className="text-muted">Describe your product…</span><Link href="/assistant" className="button button-primary">Ask <span aria-hidden>→</span></Link></div>}
  </PreviewFrame>;
}

export function ExplorerPreview({ kind }: { kind: "standards" | "sources" | "laboratories" }) {
  if (kind === "sources") return <PreviewFrame title="Sources & documents"><div className="source-layout"><div className="source-outline"><p className="eyebrow">Contents</p><p>1. Scope</p><p>2. References</p><p className="text-ink">4. Requirements</p><p>5. Testing</p></div><div><span className="pill">Source viewer</span><h4 className="mt-6 text-xl">Read the context.</h4><p className="preview-copy">Inspect available clause text alongside document metadata and provenance.</p><div className="source-lines" aria-hidden><i /><i /><i /><i /></div><EvidenceExample /></div></div></PreviewFrame>;
  const standards = kind === "standards";
  return <PreviewFrame title={standards ? "Standards Explorer" : "Laboratory search"}>
    <div className="preview-search"><span>{standards ? "Stainless steel pressure cookers" : "Search by standard or testing capability"}</span><span aria-hidden>⌕</span></div>
    <div className="my-5 flex flex-wrap gap-2">{(standards ? ["Sector", "Status", "Year"] : ["Standard", "Location", "Recognition"]).map(x => <span className="pill" key={x}>{x} <span aria-hidden className="ml-3">⌄</span></span>)}</div>
    {[0, 1].map(i => <div className="preview-result" key={i}><div className="flex items-center justify-between gap-3"><span className="font-mono text-xs text-chroma">{standards ? "Indian Standard" : "Testing capability"}</span><span className="pill">{i ? "Review details" : "Check scope"}</span></div><h4 className="mt-4 text-lg">{standards ? (i ? "Related material specifications" : "Product specification and scope") : (i ? "Performance testing" : "Material testing")}</h4><p className="preview-copy">{standards ? "Scope, amendments and linked source documents." : "Review location, listed tests and recognition details."}</p></div>)}
  </PreviewFrame>;
}
