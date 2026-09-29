"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ErrorState, Skeletons } from "@/components/ErrorState";
import { EvidenceSearchResults } from "@/components/SearchResults";
import { retrieveEvidence } from "@/lib/api/client";
import type { RetrievalEvidence } from "@/lib/api/types";

export default function SearchEvidence({ initialQuery }: { initialQuery: string }) {
  const router = useRouter();
  const [query, setQuery] = useState(initialQuery);
  const [submitted, setSubmitted] = useState(initialQuery);
  const [results, setResults] = useState<RetrievalEvidence[]>([]);
  const [loading, setLoading] = useState(Boolean(initialQuery));
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (value: string) => {
    const clean = value.trim();
    if (!clean) return;
    setSubmitted(clean);
    setLoading(true);
    setError(null);
    try {
      const response = await retrieveEvidence(clean);
      setResults(response.results);
    } catch (reason) {
      setResults([]);
      setError(reason instanceof Error ? reason.message : "The retrieval service is unavailable.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (initialQuery) void run(initialQuery);
  }, [initialQuery, run]);

  return (
    <section className="page-wrap">
      <p className="eyebrow">Verified evidence</p>
      <h1 className="page-heading mt-3">Search Indian Standards</h1>
      <p className="mt-4 max-w-2xl text-base leading-7 text-muted">Retrieve relevant BIS clauses and source passages. This view returns evidence only; it does not generate an answer.</p>
      <form role="search" className="panel mt-8 flex flex-col gap-3 sm:flex-row" onSubmit={(event) => { event.preventDefault(); const clean = query.trim(); if (!clean) return; if (clean === initialQuery) { void run(clean); return; } setSubmitted(clean); setLoading(true); setError(null); router.replace(`/search?q=${encodeURIComponent(clean)}`, { scroll: false }); }}>
        <label htmlFor="evidence-search" className="sr-only">Search verified evidence</label>
        <input id="evidence-search" type="search" className="field" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Ask about a standard, clause, test, or product..." />
        <button className="button button-primary" type="submit" disabled={loading || !query.trim()}>{loading ? "Retrieving…" : "Retrieve evidence"}</button>
      </form>
      {loading && <Skeletons rows={5} />}
      {!loading && error && <ErrorState message={error} onRetry={() => void run(submitted)} />}
      {!loading && !error && submitted && results.length === 0 && <div className="panel mt-8 py-14 text-center"><h2 className="text-xl">No verified evidence found for this query.</h2><p className="mt-3 text-sm text-muted">Try a precise IS number, clause number, product, or test name.</p></div>}
      {!loading && !error && results.length > 0 && <div className="mt-8"><p className="mb-4 text-xs text-muted" aria-live="polite">{results.length} verified evidence result{results.length === 1 ? "" : "s"} for “{submitted}”</p><EvidenceSearchResults results={results} /></div>}
    </section>
  );
}
