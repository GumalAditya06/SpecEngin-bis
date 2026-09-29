"use client";

import { useState, useEffect, useCallback, useTransition } from "react";
import { useRouter } from "next/navigation";

import { StatsRow } from "@/components/StatsRow";
import { ResultEmptyState } from "@/components/SearchResults";
import { StandardsList } from "@/components/StandardsList";
import { listStandards, getStats } from "@/lib/api/client";
import type { StandardSummary, Stats } from "@/lib/api";

const LIMIT = 50;

export default function StandardsContent({
  initial,
  q: initialQ,
}: {
  initial: { results: StandardSummary[]; total: number };
  q: string;
}) {
  const router = useRouter();
  const [q, setQ] = useState(initialQ);
  const [results, setResults] = useState<StandardSummary[]>(initial.results);
  const [total, setTotal] = useState(initial.total);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [pending, startTransition] = useTransition();

  const fetchPage = useCallback(
    (query: string, off: number) => {
      setLoading(true);
      setError(null);
      listStandards({ q: query || undefined, limit: LIMIT, offset: off })
        .then((res) => {
          setResults(res.results);
          setTotal(res.total);
          setOffset(off);
        })
        .catch((err) => setError(err.message))
        .finally(() => setLoading(false));
    },
    []
  );

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchPage(q, 0);
  }, [q, fetchPage]);

  useEffect(() => {
    getStats()
      .then(setStats)
      .catch(() => {});
  }, []);

  const loadMore = () => {
    const next = offset + LIMIT;
    startTransition(() => fetchPage(q, next));
  };

  const submitSearch = (val: string) => {
    setQ(val);
    window.location.href = val
      ? `/standards?q=${encodeURIComponent(val)}`
      : "/standards";
  };

  const selectStd = useCallback(
    (id: string) => router.push(`/standards/${id}`),
    [router]
  );

  return (
    <section className="relative scroll-mt-24">
      <div className="mx-auto max-w-[1200px] px-6 pt-24 md:px-8 md:pt-32">
        <div className="mx-auto max-w-[760px] text-center">
          <h1 className="text-[clamp(2.75rem,6vw,4.5rem)] font-medium leading-[1.02] tracking-[-0.03em] text-ink">
            Standards Explorer
          </h1>
          <p className="mx-auto mt-5 max-w-[52ch] text-lg leading-8 text-muted">
            Browse every Indian Standard, QCO notification and certification
            scheme in the index. Each result opens into its clause tree.
          </p>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            const input = (
              e.currentTarget.elements.namedItem("q") as HTMLInputElement
            );
            submitSearch(input?.value ?? "");
          }}
          className="mx-auto mt-8 flex max-w-[820px] flex-col gap-3 sm:flex-row"
          role="search"
        >
          <label htmlFor="standards-q" className="sr-only">
            Search standards
          </label>
          <input
            id="standards-q"
            name="q"
            type="search"
            defaultValue={q}
            placeholder="IS 8921 · water heaters · Ministry of Steel"
            className="h-12 flex-1 rounded-full border border-line bg-surface px-6 text-[0.9375rem] text-ink placeholder:text-faint outline-none transition-colors focus:border-line-strong focus:ring-2 focus:ring-white/40 focus:ring-offset-2 focus:ring-offset-page"
          />
          <button
            type="submit"
            className="h-12 rounded-full bg-accent px-7 text-[0.9375rem] font-medium text-accent-fg transition-opacity hover:opacity-90"
          >
            Search
          </button>
        </form>

        {loading && (
          <div className="mx-auto mt-8 max-w-[820px] space-y-3">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-20 rounded-[var(--r-card)] border border-line bg-surface"
                aria-hidden
              />
            ))}
          </div>
        )}

        {error && (
          <div className="mx-auto mt-8 max-w-[820px] rounded-[var(--r-card)] border border-line bg-surface px-5 py-4 text-sm text-muted">
            {error}
            <button
              onClick={() => fetchPage(q, offset)}
              className="ml-4 rounded-full border border-line px-4 py-1.5 text-[0.8125rem] text-ink transition-colors hover:border-line-strong"
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !error && (
          <>
            <div className="mx-auto mt-6 flex max-w-[820px] items-center justify-between text-sm text-faint">
              <span aria-live="polite">
                {total} standard{total === 1 ? "" : "s"}
                {q ? ` for “${q}”` : ""}
              </span>
            </div>

            <div className="mx-auto mt-4 max-w-[820px]">
              {total === 0 ? (
                <ResultEmptyState query={q} />
              ) : (
                <>
                  <StandardsList results={results} onSelect={selectStd} />
                  {results.length < total && (
                    <div className="mt-6 text-center">
                      <button
                        onClick={loadMore}
                        disabled={pending}
                        className="inline-flex h-11 items-center rounded-full border border-line px-6 text-[0.9375rem] text-ink transition-colors hover:border-line-strong disabled:opacity-50"
                      >
                        {pending
                          ? "Loading…"
                          : `Load more (${Math.max(0, total - results.length)} remaining)`}
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          </>
        )}

        <StatsRow stats={stats} />
      </div>
    </section>
  );
}
