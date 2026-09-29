"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { listStandards, searchDocuments } from "@/lib/api/client";
import { useAsyncData } from "@/lib/api/useAsyncData";
import type { SearchResponse, StandardSummary } from "@/lib/api/types";
import SourceCard from "@/components/sources/SourceCard";
import { filterSources, sourceFacets } from "@/lib/sources/filters";
import {
  ACCESS_OPTIONS,
  SOURCE_TYPE_OPTIONS,
  toSourceRecord,
} from "@/lib/sources/records";
import { ErrorState, Skeletons } from "./ErrorState";

type Kind = "standards" | "sources";
const titles = {
  standards: "Standards explorer",
  sources: "Source library",
};
const descriptions = {
  standards:
    "Find a standard, understand its scope,\nand trace the evidence to your next step.",
  sources:
    "Read the standards, schemes and official\ndocuments behind your answers.",
};
const PAGE_SIZE = 6;
// Search, document type and access are server-side. Year and organization are
// not parameters of /api/v1/search, so the library reads the largest page the
// endpoint allows and refines those two facets locally.
const SOURCE_FETCH_SIZE = 100;
export default function Catalogue({
  kind,
  query,
}: {
  kind: Kind;
  query: Record<string, string | undefined>;
}) {
  const router = useRouter();
  const [draft, setDraft] = useState(query.q || "");
  const page = Math.max(0, Number(query.page) || 0);
  const params = {
    q: query.q,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const year = query.year ? Number(query.year) : undefined;
  const { data, loading, error, retry } = useAsyncData(async () => {
    if (kind === "standards")
      return listStandards({
        ...params,
        sector: query.sector,
        status: query.status,
        year: Number.isFinite(year) ? year : undefined,
        sort: query.sort,
        order: query.order,
      });
    return searchDocuments({
      q: query.q,
      source_type: query.source_type,
      licence: query.licence,
      limit: SOURCE_FETCH_SIZE,
      offset: 0,
    });
  });
  const sources = useMemo(() => {
    if (kind !== "sources") return [];
    return ((data as SearchResponse | null)?.results ?? []).map(toSourceRecord);
  }, [kind, data]);
  const facets = useMemo(() => sourceFacets(sources), [sources]);
  const visibleSources = useMemo(
    () =>
      filterSources(sources, {
        year: query.year,
        organization: query.publisher,
      }),
    [sources, query.year, query.publisher],
  );
  const refining = Boolean(query.year || query.publisher);
  const total = data?.total ?? 0;
  const shown = kind === "sources" && refining ? visibleSources.length : total;
  const available = kind === "sources" ? visibleSources.length : total;
  function update(values: Record<string, string>) {
    const p = new URLSearchParams();
    Object.entries({ ...query, page: "0", ...values }).forEach(([k, v]) => {
      if (v && v !== "0") p.set(k, v);
    });
    const search = p.toString();
    router.push(search ? `/${kind}?${search}` : `/${kind}`);
  }
  const select = (
    label: string,
    name: string,
    options: [string, string][],
    allLabel: string,
  ) => (
    <label className="min-w-0 text-xs text-muted">
      {label}
      <select
        className="field mt-2"
        // A wrapped label would otherwise take its name from the option text.
        aria-label={label}
        value={query[name] || ""}
        onChange={(e) => update({ [name]: e.target.value })}
      >
        <option value="">{allLabel}</option>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <section className="page-wrap">
      <p className="eyebrow">Knowledge library</p>
      <h1 className="page-heading mt-3">{titles[kind]}</h1>
      <p className="mt-4 max-w-2xl whitespace-pre-line text-base leading-7 text-muted">
        {descriptions[kind]}
      </p>
      <div className="panel mt-8">
        <form
          role="search"
          className="flex flex-col gap-3 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            update({ q: draft });
          }}
        >
          <label htmlFor="catalogue-search" className="sr-only">
            Search {kind}
          </label>
          <input
            id="catalogue-search"
            type="search"
            className="field"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Search by title, product, or IS number..."
          />
          <button className="button button-primary" type="submit">
            Search
          </button>
          <button
            className="button"
            type="button"
            onClick={() => router.push(`/${kind}`)}
          >
            Reset
          </button>
        </form>
        <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {kind === "standards" && (
            <>
              <label className="min-w-0 text-xs text-muted sm:col-span-3">
                Product / keyword
                <input
                  className="field mt-2"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="e.g. pressure cooker"
                />
              </label>
              {select("Sector", "sector", [
                ...(!query.sector || ["Metallurgy", "Fire Safety", "Electrical Appliances", "Electronics"].includes(query.sector)
                  ? []
                  : [[query.sector, query.sector] as [string, string]]),
                ["Metallurgy", "Metallurgy"],
                ["Fire Safety", "Fire Safety"],
                ["Electrical Appliances", "Electrical appliances"],
                ["Electronics", "Electronics"],
              ], "All sectors")}
              {select("Status", "status", [
                ["current", "Current"],
                ["withdrawn", "Withdrawn"],
              ], "All statuses")}
              <label className="min-w-0 text-xs text-muted">
                Year
                <input
                  className="field mt-2"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  defaultValue={query.year || ""}
                  placeholder="Any year"
                  onBlur={(e) => {
                    if (e.target.value !== (query.year || ""))
                      update({ year: e.target.value });
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      update({ year: e.currentTarget.value });
                    }
                  }}
                />
              </label>
            </>
          )}
          {kind === "sources" && (
            <>
              {select("Document type", "source_type", SOURCE_TYPE_OPTIONS, "All document types")}
              {select("Year", "year", facets.years.map((y) => [String(y), String(y)]), "Any year")}
              {select("Source organization", "publisher", facets.organizations.map((o) => [o, o]), "All organizations")}
              {select("Access level", "licence", ACCESS_OPTIONS, "Any access level")}
            </>
          )}
        </div>
      </div>
      {loading && <Skeletons />}
      {error && <ErrorState message={error} onRetry={retry} />}
      {!loading && !error && data && (
        <>
          <div className="my-6 text-xs text-muted">
            <p aria-live="polite">
              {shown} result{shown !== 1 ? "s" : ""}
              {query.q ? ` for “${query.q}”` : ""}
              {kind === "sources" && refining ? ` of ${total}` : ""}
            </p>
          </div>
          {available === 0 ? (
            <div className="panel py-14 text-center">
              <h2 className="text-xl">No matching {kind}</h2>
              <p className="mt-3 text-sm text-muted">
                Try a broader search or remove a filter.
              </p>
              <button
                className="button mt-6"
                onClick={() => router.push(`/${kind}`)}
              >
                Clear filters
              </button>
            </div>
          ) : kind === "sources" ? (
            <div className="grid gap-4">
              {visibleSources
                .slice(
                  page * PAGE_SIZE,
                  page * PAGE_SIZE + PAGE_SIZE,
                )
                .map((source) => (
                  <SourceCard key={source.id} source={source} />
                ))}
            </div>
          ) : (
            <div className="grid gap-4">
              {data.results.map((row) => {
                const s = row as StandardSummary;
                return (
                  <Link
                    key={s.id}
                    href={`/standards/${s.id}`}
                    className="panel standard-result transition-colors hover:border-line-strong"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <span className="font-mono text-xs text-chroma">
                        {s.is_number}
                      </span>
                      <span className="pill">{s.status}</span>
                    </div>
                    <h2 className="mt-3 text-xl font-medium">{s.title}</h2>
                    <p className="mt-2 text-sm leading-6 text-muted">
                      {s.description ||
                        "Open the standard to review scope, linked sources and related evidence."}
                    </p>
                    <div className="mt-5 flex flex-wrap justify-between gap-3 text-xs text-faint">
                      <span>
                        {[s.sector, s.year, s.status]
                          .filter(Boolean)
                          .join(" · ")}
                        {" · "}
                        {s.document_count} source document
                        {s.document_count === 1 ? "" : "s"}
                      </span>
                      <span className="text-ink">Review standard →</span>
                    </div>
                  </Link>
                );
              })}
            </div>
          )}
          {available > PAGE_SIZE && (
            <nav
              aria-label="Pagination"
              className="mt-6 flex items-center justify-between"
            >
              <button
                className="button"
                disabled={page === 0}
                onClick={() => update({ page: String(page - 1) })}
              >
                ← Previous
              </button>
              <span className="text-xs text-muted">
                Page {page + 1} of {Math.ceil(available / PAGE_SIZE)}
              </span>
              <button
                className="button"
                disabled={(page + 1) * PAGE_SIZE >= available}
                onClick={() => update({ page: String(page + 1) })}
              >
                Next →
              </button>
            </nav>
          )}
        </>
      )}
    </section>
  );
}
