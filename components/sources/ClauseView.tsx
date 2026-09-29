import Link from "next/link";
import { assistantHref, isFullText } from "@/lib/sources/records";
import type { SourceClause, SourceRecord } from "@/lib/sources/types";

function References({ references }: { references: NonNullable<SourceClause["referencedBy"]> }) {
  return (
    <div className="mt-6 rounded-[var(--r-inner)] border border-line bg-page p-4">
      <p className="eyebrow">Referenced by Specengine</p>
      {references.length ? (
        <ul className="mt-3 space-y-2 text-sm text-muted">
          {references.map((reference) => (
            <li key={`${reference.kind}-${reference.id}`}>{reference.label}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm leading-7 text-muted">
          No answer, standard, service or laboratory result references this
          clause yet.
        </p>
      )}
    </div>
  );
}

/**
 * A single clause in reading order: number, title, extracted text, and the
 * hand-off back to the Assistant. Text is only rendered when the source's
 * licence class permits it.
 */
export default function ClauseView({
  source,
  clause,
  onOpen,
}: {
  source: SourceRecord;
  clause: SourceClause;
  onOpen?: (clause: SourceClause) => void;
}) {
  const full = isFullText(source.access);
  return (
    <article
      id={`clause-${clause.index}`}
      data-clause-number={clause.number ?? ""}
      tabIndex={-1}
      className="panel source-clause scroll-mt-24"
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-mono text-xs text-chroma">
          {clause.number ? `Clause ${clause.number}` : `Section ${clause.index + 1}`}
        </span>
        {clause.title && (
          <h2 className="text-lg font-medium">{clause.title}</h2>
        )}
      </div>
      <p className="mt-4 max-w-[68ch] whitespace-pre-wrap text-[0.9375rem] leading-8 text-muted">
        {clause.text ||
          (full
            ? "No text has been extracted for this clause."
            : "Clause metadata only. Open the original source to read the published text.")}
      </p>
      {clause.referencedBy !== undefined && (
        <References references={clause.referencedBy} />
      )}
      <div className="mt-6 flex flex-wrap gap-3 border-t border-line pt-5">
        <Link className="button" href={assistantHref(source, clause.number)}>
          Ask Assistant about this clause →
        </Link>
        {onOpen && (
          <button className="button" onClick={() => onOpen(clause)}>
            Inspect source →
          </button>
        )}
      </div>
    </article>
  );
}
