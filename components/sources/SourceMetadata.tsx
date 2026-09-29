import { accessLabel, formatDate } from "@/lib/sources/records";
import type { SourceRecord } from "@/lib/sources/types";
import { trustedSourceUrl } from "@/lib/sources/url";

export type MetadataRow = [string, string];

/**
 * The shared source metadata block — the same rows, order and dividers in the
 * source drawer and on the source detail page. Unknown values are omitted
 * rather than shown as placeholders.
 */
export interface MetadataContext {
  clause?: string | null;
  page?: string | number | null;
  section?: string | null;
}

/** A row is dropped when the backend has no value for it. */
function row(term: string, value: string | null | undefined): MetadataRow | null {
  const text = typeof value === "string" ? value.trim() : "";
  return text ? [term, text] : null;
}

export function metadataRows(
  source: SourceRecord,
  extra: MetadataContext = {},
): MetadataRow[] {
  const rows: (MetadataRow | null)[] = [
    row("Document type", source.category),
    row("Organization", source.organization),
    row("Version", source.version != null ? `Version ${source.version}` : null),
    row("IS number", source.isNumber),
    row("Date", source.publishedOn),
    row("Access", source.access ? accessLabel(source.access) : null),
    row("Clauses", source.clauseCount != null ? String(source.clauseCount) : null),
    row("Retrieved", formatDate(source.retrievedAt)),
    row("Clause", extra.clause),
    row("Page", extra.page != null && extra.page !== "" ? String(extra.page) : null),
    row("Section", extra.section),
  ];
  return rows.filter((entry): entry is MetadataRow => entry !== null);
}

export default function SourceMetadata({
  source,
  clause,
  page,
  section,
  className = "mt-5",
}: {
  source: SourceRecord;
  clause?: string | null;
  page?: string | number | null;
  section?: string | null;
  className?: string;
}) {
  const rows = metadataRows(source, { clause, page, section });
  const original = trustedSourceUrl(source.url);
  if (!rows.length && !original) return null;
  return (
    <>
      {rows.length > 0 && (
        <dl className={`${className} divide-y divide-line border-y border-line`}>
          {rows.map(([term, value]) => (
            <div
              key={term}
              className="flex items-baseline justify-between gap-4 py-3"
            >
              <dt className="shrink-0 text-xs text-faint">{term}</dt>
              <dd className="min-w-0 break-words text-right text-sm text-muted">
                {value}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {original && (
        <a
          className="mt-4 inline-flex text-xs text-chroma underline underline-offset-4"
          href={original}
          target="_blank"
          rel="noopener noreferrer"
        >
          Open original source →
        </a>
      )}
    </>
  );
}
