"use client";

import { useEffect, useRef, useState } from "react";
import SourceDrawer from "@/components/sources/SourceDrawer";
import Answer from "@/components/assistant/Answer";
import RecentQuestions, { HISTORY_KEY, readQuestions, type RecentQuestion } from "@/components/assistant/RecentQuestions";
import ResearchProgress from "@/components/assistant/ResearchProgress";
import { assistantQueryStream, type StreamStage } from "@/lib/api/client";
import type { AssistantCitation, AssistantResponse } from "@/lib/api";
import { sourceFromCitation, sourceQuestion } from "@/lib/sources/records";
import type { SourceRecord } from "@/lib/sources/types";
import "@/components/assistant/assistant.css";

const EXAMPLES = [
  "Which standard applies to stainless steel pressure cookers?",
  "What BIS certification is required for my product?",
  "What tests are required for steel wire ropes?",
  "Find a testing laboratory for this standard.",
  "How does BIS hallmarking work?",
];

export default function AssistantPage({ initialQuery = "" }: { initialQuery?: string }) {
  const [query, setQuery] = useState(initialQuery);
  const [submitted, setSubmitted] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<AssistantResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState<StreamStage | null>(null);
  const [citation, setCitation] = useState<AssistantCitation | null>(null);
  const [questions, setQuestions] = useState<RecentQuestion[]>([]);
  const input = useRef<HTMLTextAreaElement>(null);
  const requestId = useRef(0);
  const busy = useRef(false);
  const cached = useRef(new Map<string, AssistantResponse>());
  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => { if (active) setQuestions(readQuestions()); });
    return () => { active = false; requestId.current += 1; };
  }, []);

  function remember(question: string) {
    const next = [{ question, timestamp: Date.now() }, ...readQuestions().filter(item => item.question !== question)].slice(0, 12);
    setQuestions(next);
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(next)); } catch { /* The current answer remains usable without storage. */ }
  }
  async function submit(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy.current) return;
    busy.current = true;
    const id = ++requestId.current;
    setSubmitted(trimmed); setQuery(""); setResult(null); setError(null); setStage(null); setPending(true);
    remember(trimmed);
    requestAnimationFrame(() => document.getElementById("submitted-question")?.scrollIntoView({ block: "start" }));
    try {
      const response = await assistantQueryStream({ query: trimmed }, (next) => { if (requestId.current === id) setStage(next); });
      if (requestId.current !== id) return;
      cached.current.set(trimmed, response);
      if (cached.current.size > 12) cached.current.delete(cached.current.keys().next().value!);
      setResult(response);
    } catch (err) {
      if (requestId.current === id) { setError(err instanceof Error ? err.message : "The request could not be completed."); setQuery(trimmed); }
    } finally {
      if (requestId.current === id) { busy.current = false; setPending(false); setStage(null); }
    }
  }
  function openQuestion(question: string) {
    if (busy.current) return;
    const answer = cached.current.get(question);
    if (answer) { setSubmitted(question); setResult(answer); setError(null); setQuery(""); requestAnimationFrame(() => document.getElementById("submitted-question")?.scrollIntoView({ block: "start" })); }
    else { setQuery(question); requestAnimationFrame(() => input.current?.focus()); }
  }
  function askSource(source: SourceRecord, context: { clause?: string | null; page?: string | number | null }) {
    setCitation(null);
    setQuery(sourceQuestion(source.isNumber || source.title, context.clause, context.page));
    requestAnimationFrame(() => input.current?.focus());
  }
  const composer = <form className="research-composer" onSubmit={e => { e.preventDefault(); void submit(query); }}>
    <label htmlFor="assistant-q" className="sr-only">Describe your product or ask a question</label>
    <textarea id="assistant-q" ref={input} rows={2} maxLength={2000} value={query} disabled={pending} onChange={e => setQuery(e.target.value)} placeholder="Describe your product or ask a question..." onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} />
    <div className="composer-actions"><span className="text-xs text-muted">{pending ? "Researching your question…" : "Product · Standard · BIS service"}</span><button className="button button-primary" disabled={pending || !query.trim()} type="submit">{pending ? "Researching…" : "Ask"} <span aria-hidden>→</span></button></div>
  </form>;
  return <section className={`assistant-research page-wrap ${submitted ? "has-question" : ""}`}>
    <div className="research-toolbar"><span className="eyebrow">BIS intelligence</span>{submitted && <button className="button" disabled={pending} onClick={() => { setSubmitted(""); setResult(null); setError(null); setQuery(""); requestAnimationFrame(() => input.current?.focus()); }}>New question <span aria-hidden>+</span></button>}</div>
    {!submitted && <div className="assistant-intro"><h1 className="page-heading">Ask about Indian Standards.</h1><p>Find standards, understand requirements,<br className="hidden sm:block" /> explore BIS services and trace answers back<br className="hidden sm:block" /> to authoritative sources.</p></div>}
    {submitted && <h1 className="sr-only">Specengine BIS research assistant</h1>}
    <div className="research-layout"><div className="research-main">
      {!submitted && <>{composer}<div className="context-prompts" aria-label="Suggested questions">{EXAMPLES.map(example => <button key={example} onClick={() => void submit(example)} className="prompt-chip">{example}<span aria-hidden>↗</span></button>)}</div></>}
      {submitted && <><div id="submitted-question" className="submitted-question"><p className="eyebrow">Your question</p><p className="mt-3 text-base leading-7">{submitted}</p></div>
        {pending && <ResearchProgress stage={stage} />}
        {error && <div role="alert" className="research-error"><h2 className="text-xl font-medium">The research could not be completed.</h2><p className="mt-3 text-sm leading-7 text-muted">{error}</p><button className="button mt-5" onClick={() => void submit(submitted)}>Try again</button><p className="mt-4 text-xs text-muted">Your question is saved above. You can also edit it below.</p></div>}
        {result && <Answer key={submitted} result={result} onSource={setCitation} />}
        <div className="followup-composer">{composer}</div>
      </>}
    </div><aside className="research-history"><RecentQuestions questions={questions} disabled={pending} onSelect={openQuestion} onClear={() => { setQuestions([]); cached.current.clear(); try { localStorage.removeItem(HISTORY_KEY); } catch { /* Storage can be unavailable. */ } }} /></aside></div>
    <SourceDrawer
      source={citation ? sourceFromCitation(citation) : null}
      clause={citation?.clause}
      page={citation?.page}
      section={citation?.section}
      excerpt={citation?.excerpt}
      onClose={() => setCitation(null)}
      onAsk={askSource}
    />
  </section>;
}
