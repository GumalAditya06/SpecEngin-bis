"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { listLaboratories } from "@/lib/api/client";
import { useAsyncData } from "@/lib/api/useAsyncData";
import {
  loadLaboratoryRecords,
  loadLaboratoryStandardOptions,
} from "@/lib/laboratories/load";
import {
  capabilitySuggestions,
  laboratoryFacets,
} from "@/lib/laboratories/filters";
import { toLaboratoryRecord } from "@/lib/laboratories/records";
import LaboratoryCard from "./LaboratoryCard";
import { ErrorState, Skeletons } from "../ErrorState";

const PAGE_SIZE = 6;
/**
 * The city and recognition-status facets are read from the whole catalogue
 * rather than the filtered page, so switching from one city or status to
 * another stays possible. `limit` is the endpoint maximum.
 */
const CORPUS_SIZE = 100;

/**
 * The filter the user last changed, with the URL it produced.
 *
 * A filter change navigates, and the page remounts this component with the new
 * parameters, which would otherwise drop keyboard focus to the document body
 * and force a keyboard user back to the top of the page. Module scope because
 * the remount clears component state. The URL travels with it so that a mount
 * reached any other way — a direct visit, a back navigation, a card click — is
 * never pulled into a filter.
 */
let pendingFilter: { label: string; url: string } | null = null;

/**
 * The laboratory directory: search, four filters, and the
 * standard → capability relationship on every card.
 *
 * The layout, filters and pagination are the catalogue's existing system, and
 * comparison is deliberately absent — the MVP answers "search, filter,
 * understand the capability, then open the laboratory".
 */
export default function LaboratoryCatalogue({
  query,
}: {
  query: Record<string, string | undefined>;
}) {
  const router = useRouter();
  const [draft, setDraft] = useState(query.q || "");
  const page = Math.max(0, Number(query.page) || 0);
  const { data, loading, error, retry } = useAsyncData(async () => {
    // The corpus powers the facet options; a failure there must not hide the
    // results, so it falls back to the page that was actually returned.
    const [pageResult, corpus, standardOptions] = await Promise.all([
      listLaboratories({
        q: query.q,
        city: query.city,
        recognition_status: query.recognition_status,
        test: query.test,
        standard_id: query.standard_id,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
      listLaboratories({ limit: CORPUS_SIZE, offset: 0 }).catch(() => null),
      loadLaboratoryStandardOptions(query.standard_id).catch(() => []),
    ]);
    return {
      total: pageResult.total,
      records: await loadLaboratoryRecords(pageResult.results),
      corpus: (corpus?.results ?? []).map((summary) => toLaboratoryRecord(summary)),
      standardOptions,
    };
  });
  const facets = useMemo(() => laboratoryFacets(data?.corpus ?? []), [data]);
  const capabilities = useMemo(
    () => capabilitySuggestions(data?.records ?? []),
    [data],
  );
  const total = data?.total ?? 0;
  const standardOptions = data?.standardOptions ?? [];

  // Focus follows the filter the user just used, once its results are in.
  useEffect(() => {
    if (!data || !pendingFilter) return;
    const pending = pendingFilter;
    pendingFilter = null;
    if (pending.url !== window.location.pathname + window.location.search) return;
    document
      .querySelector<HTMLElement>(`[aria-label="${pending.label}"]`)
      ?.focus();
  }, [data]);

  function update(values: Record<string, string>, filter?: string) {
    const params = new URLSearchParams();
    // "0" is the default page, so it is dropped rather than left in the URL.
    Object.entries({ ...query, page: "0", ...values }).forEach(([key, value]) => {
      if (value && value !== "0") params.set(key, value);
    });
    const search = params.toString();
    const target = search ? `/laboratories?${search}` : "/laboratories";
    pendingFilter = filter ? { label: filter, url: target } : null;
    router.push(target);
  }

  function reset() {
    pendingFilter = null;
    router.push("/laboratories");
  }

  function select(
    label: string,
    name: string,
    options: [string, string][],
    allLabel: string,
  ) {
    return (
      <label className="min-w-0 text-xs text-muted">
        {label}
        <select
          className="field mt-2"
          // A wrapped label would otherwise take its name from the option text.
          aria-label={label}
          value={query[name] || ""}
          onChange={(event) => update({ [name]: event.target.value }, label)}
        >
          <option value="">{allLabel}</option>
          {options.map(([value, text]) => (
            <option key={value} value={value}>
              {text}
            </option>
          ))}
        </select>
      </label>
    );
  }

  return (
    <section className="page-wrap">
      <p className="eyebrow">Testing network</p>
      <h1 className="page-heading mt-3">Find a testing laboratory</h1>
      <p className="mt-4 max-w-2xl whitespace-pre-line text-base leading-7 text-muted">
        {"Explore laboratory capabilities relevant\nto your product and standard."}
      </p>

      <div className="panel mt-8">
        <form
          role="search"
          className="flex flex-col gap-3 sm:flex-row"
          onSubmit={(event) => {
            event.preventDefault();
            update({ q: draft });
          }}
        >
          <label htmlFor="laboratory-search" className="sr-only">
            Search laboratories
          </label>
          <input
            id="laboratory-search"
            type="search"
            className="field"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Search laboratory name..."
          />
          <button className="button button-primary" type="submit">
            Search
          </button>
          <button
            className="button"
            type="button"
            onClick={reset}
          >
            Reset
          </button>
        </form>

        <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {select(
            "City",
            "city",
            facets.cities.map((city) => [city, city]),
            "All cities",
          )}
          {select(
            "Recognition status",
            "recognition_status",
            facets.statuses,
            "All statuses",
          )}
          <label className="min-w-0 text-xs text-muted">
            Testing capability
            <input
              className="field mt-2"
              list="laboratory-capabilities"
              // Matches the selects: the wrapped label alone would take its
              // name from any sibling text, so every filter is named here.
              aria-label="Testing capability"
              defaultValue={query.test || ""}
              placeholder="Any capability"
              onBlur={(event) => {
                if (event.target.value !== (query.test || ""))
                  update({ test: event.target.value }, "Testing capability");
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter")
                  update(
                    { test: event.currentTarget.value },
                    "Testing capability",
                  );
              }}
            />
            <datalist id="laboratory-capabilities">
              {capabilities.map((capability) => (
                <option key={capability} value={capability} />
              ))}
            </datalist>
          </label>
          {select(
            "Standard",
            "standard_id",
            standardOptions.map((option) => [
              option.id,
              `${option.isNumber} — ${option.laboratoryCount} ${
                option.laboratoryCount === 1 ? "laboratory" : "laboratories"
              }`,
            ]),
            "All standards",
          )}
        </div>
      </div>

      {loading && <Skeletons />}
      {error && <ErrorState message={error} onRetry={retry} />}

      {!loading && !error && data && (
        <>
          <div className="my-6 text-xs text-muted">
            <p aria-live="polite">
              {total} laborator{total === 1 ? "y" : "ies"}
              {query.q ? ` for “${query.q}”` : ""}
            </p>
          </div>

          {!data.records.length ? (
            <div className="panel py-14 text-center">
              <h2 className="text-xl">
                {query.standard_id
                  ? "No laboratory for this standard"
                  : "No matching laboratories"}
              </h2>
              <p className="mt-3 text-sm text-muted">
                {query.standard_id
                  ? "No indexed laboratory is recognized for the selected standard yet."
                  : "Try a broader search or remove a filter."}
              </p>
              <button
                className="button mt-6"
                onClick={reset}
              >
                Clear filters
              </button>
            </div>
          ) : (
            <div className="laboratory-results grid gap-4">
              {data.records.map((record) => (
                <LaboratoryCard key={record.id} record={record} />
              ))}
            </div>
          )}

          {total > PAGE_SIZE && (
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
                Page {page + 1} of {Math.ceil(total / PAGE_SIZE)}
              </span>
              <button
                className="button"
                disabled={(page + 1) * PAGE_SIZE >= total}
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
