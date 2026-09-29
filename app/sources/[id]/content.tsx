"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import ClauseView from "@/components/sources/ClauseView";
import SourceDrawer, {
  type SourceDrawerContext,
} from "@/components/sources/SourceDrawer";
import SourceMetadata from "@/components/sources/SourceMetadata";
import SourceTableOfContents from "@/components/sources/SourceTableOfContents";
import {
  assistantHref,
  isFullText,
  toSourceReading,
} from "@/lib/sources/records";
import type { SourceReading } from "@/lib/sources/types";
import { trustedSourceUrl } from "@/lib/sources/url";
import type { DocumentDetail } from "@/lib/api";

const TABS = ["Clauses", "Full text", "Source record"] as const;

export default function SourceContent({
  doc,
  initialClause,
}: {
  doc: DocumentDetail;
  initialClause: string | null;
}) {
  const reading: SourceReading = toSourceReading(doc);
  const originalUrl = trustedSourceUrl(reading.url);
  // A clause handed over from the Assistant, a card or the drawer deep-links
  // into the list. The clause is resolved as initial state rather than pushed
  // from an effect, so the first paint already has the right clause active.
  const handoff = useMemo(
    () =>
      initialClause
        ? (reading.clauses.find((clause) => clause.number === initialClause) ??
          null)
        : null,
    [initialClause, reading.clauses],
  );
  const [tab, setTab] = useState<(typeof TABS)[number]>("Clauses");
  const [query, setQuery] = useState("");
  const [active, setActive] = useState<number | null>(handoff?.index ?? null);
  const [drawer, setDrawer] = useState<{
    clause: string | null;
    context: SourceDrawerContext;
  } | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const full = isFullText(reading.access);

  // Scrolling into the handed-over clause is a DOM concern, not state.
  useEffect(() => {
    if (!handoff) return;
    const target = listRef.current?.querySelector<HTMLElement>(
      `#clause-${handoff.index}`,
    );
    target?.scrollIntoView({ block: "start" });
    target?.focus({ preventScroll: true });
  }, [handoff]);

  const needle = query.trim().toLowerCase();
  const clauses = needle
    ? reading.clauses.filter((clause) =>
        `${clause.number ?? ""} ${clause.title ?? ""} ${clause.text ?? ""}`
          .toLowerCase()
          .includes(needle),
      )
    : reading.clauses;

  const meta = doc.source?.meta ?? null;
  const metaRows = Object.entries(meta ?? {}).map(([key, value]) => [
    key.replaceAll("_", " "),
    String(value),
  ]);

  return (
    <section className="page-wrap source-reader">
      <Link href="/sources" className="text-sm text-muted">
        ← Source library
      </Link>

      <div className="mt-8 flex flex-wrap items-start justify-between gap-6">
        <div className="max-w-3xl">
          <p className="eyebrow">{reading.category}</p>
          <h1 className="mt-3 text-3xl font-medium leading-tight tracking-tight">
            {reading.title}
          </h1>
          <p className="mt-4 text-sm text-muted">
            {[
              reading.organization,
              reading.version ? `Version ${reading.version}` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {reading.isNumber && (
            <p className="mt-2 font-mono text-xs text-chroma">
              {reading.isNumber}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="pill">
            {full ? "Full text available" : "Metadata only"}
          </span>
          <Link
            className="button button-primary"
            href={assistantHref(reading)}
          >
            Ask Assistant about this source →
          </Link>
        </div>
      </div>

      <SourceMetadata source={reading} className="mt-8 max-w-3xl" />

      {reading.relatedStandards.length > 0 && (
        <div className="mt-8 max-w-3xl">
          <h2 className="text-[0.8125rem] font-medium text-ink">
            Related standards
          </h2>
          <ul className="mt-4 grid gap-3">
            {reading.relatedStandards.map((standard) => (
              <li key={standard.id || standard.is_number || "standard"}>
                {standard.id ? (
                  <Link
                    href={`/standards/${standard.id}`}
                    className="block rounded-[var(--r-inner)] border border-line bg-surface p-4 transition-colors hover:border-line-strong"
                  >
                    <span className="font-mono text-xs text-chroma">
                      {standard.is_number}
                    </span>
                    {standard.title && (
                      <span className="mt-2 block text-sm text-muted">
                        {standard.title}
                      </span>
                    )}
                  </Link>
                ) : (
                  <span className="block rounded-[var(--r-inner)] border border-line bg-surface p-4 text-sm text-muted">
                    {standard.is_number || standard.title}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-10 grid items-start gap-6 lg:grid-cols-[250px_1fr]">
        <aside className="panel lg:sticky lg:top-24">
          <h2 className="text-sm font-medium">In this source</h2>
          <SourceTableOfContents
            nodes={reading.clauseTree}
            active={active}
            onSelect={setActive}
          />
          <div className="mt-6 border-t border-line pt-5">
            <p className="text-xs text-faint">Retrieved</p>
            <p className="mt-2 text-xs text-muted">
              {reading.retrievedAt
                ? new Date(reading.retrievedAt).toLocaleDateString("en-IN")
                : "Not recorded"}
            </p>
            {originalUrl && (
              <a
                className="button mt-5 w-full"
                href={originalUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                Original source ↗
              </a>
            )}
          </div>
        </aside>

        <div className="min-w-0">
          <div className="mb-5 flex gap-2" aria-label="Source views">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`button ${tab === t ? "button-primary" : ""}`}
                aria-pressed={tab === t}
              >
                {t}
              </button>
            ))}
          </div>

          {!full && (
            <p className="mb-5 rounded-xl border border-line bg-surface p-4 text-sm leading-7 text-muted">
              This record provides metadata only. Read the original publication
              through its source link; restricted body text is not displayed
              here.
            </p>
          )}

          {tab === "Clauses" && (
            <div className="source-tab-panel" key="clauses">
              <label className="mb-5 block">
                <span className="sr-only">Find within source</span>
                <input
                  className="field"
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Find a clause or keyword in this source…"
                />
              </label>
              <div className="space-y-4" ref={listRef}>
                {clauses.map((clause) => (
                  <ClauseView
                    key={clause.index}
                    source={reading}
                    clause={clause}
                    onOpen={() =>
                      setDrawer({
                        clause: clause.number,
                        context: { clause: clause.number, excerpt: clause.text },
                      })
                    }
                  />
                ))}
                {!clauses.length && (
                  <div className="panel text-sm text-muted">
                    {reading.clauses.length
                      ? "No clauses match this search."
                      : "No clause text has been extracted for this source."}
                  </div>
                )}
              </div>
            </div>
          )}

          {tab === "Full text" && (
            <article className="panel source-tab-panel" key="full-text">
              <h2 className="text-lg font-medium">Document text</h2>
              <p className="mt-5 max-w-[68ch] whitespace-pre-wrap text-[0.9375rem] leading-8 text-muted">
                {full
                  ? reading.fullText ||
                    "Full text has not been extracted for this document."
                  : "Full text is not available under this document’s licence."}
              </p>
            </article>
          )}

          {tab === "Source record" && (
            <div className="panel source-tab-panel" key="source-record">
              <h2 className="text-lg font-medium">Source record</h2>
              {metaRows.length ? (
                <dl className="mt-5 divide-y divide-line">
                  {metaRows.map(([term, value]) => (
                    <div
                      key={term}
                      className="grid gap-2 py-4 sm:grid-cols-[140px_1fr]"
                    >
                      <dt className="text-xs capitalize text-faint">
                        {term}
                      </dt>
                      <dd className="break-words text-sm text-muted">
                        {value}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="mt-5 text-sm leading-7 text-muted">
                  No additional source metadata was recorded for this document.
                </p>
              )}
              {originalUrl && (
                <a
                  className="button mt-6"
                  href={originalUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Original source ↗
                </a>
              )}
            </div>
          )}
        </div>
      </div>

      <SourceDrawer
        source={drawer ? reading : null}
        clause={drawer?.clause ?? null}
        excerpt={drawer?.context.excerpt ?? null}
        onClose={() => setDrawer(null)}
      />
    </section>
  );
}
