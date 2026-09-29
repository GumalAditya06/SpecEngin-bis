"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";

import SourceMetadata from "@/components/sources/SourceMetadata";
import {
  accessLabel,
  assistantHref,
  isFullText,
  sourceHref,
} from "@/lib/sources/records";
import type { SourceRecord } from "@/lib/sources/types";

export interface SourceDrawerContext {
  clause?: string | null;
  page?: string | number | null;
  section?: string | null;
  excerpt?: string | null;
}

/**
 * The one source viewer in the product. Assistant, Standards, Services and
 * Laboratories all open this drawer, so metadata, badges and calls to action
 * stay identical wherever a source is referenced.
 *
 * `excerpt` carries the text that prompted the opening (an assistant excerpt or
 * a clause) so evidence can be read without leaving the current page. When a
 * clause is known, "Read full source" deep-links to that clause.
 *
 * Sources without a document id are illustrative placeholders — the landing
 * page example — and deliberately show no navigation actions.
 */
export default function SourceDrawer({
  source,
  clause,
  page,
  section,
  excerpt,
  onClose,
  onAsk,
}: {
  source: SourceRecord | null;
  clause?: string | null;
  page?: string | number | null;
  section?: string | null;
  excerpt?: string | null;
  onClose: () => void;
  onAsk?: (source: SourceRecord, context: SourceDrawerContext) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    const trigger =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    if (source && dialog && !dialog.open) {
      dialog.showModal();
      // showModal() moves focus into the dialog; put it on the close control so
      // keyboard and screen-reader users land somewhere predictable.
      closeRef.current?.focus();
    }
    return () => {
      if (dialog?.open) dialog.close();
      if (trigger?.isConnected) trigger.focus();
    };
  }, [source]);

  if (!source) return null;
  // An explicit restrictive licence suppresses extracted text. An absent
  // licence must not hide validated evidence supplied by the answer API.
  const metaOnly = Boolean(source.access) && !isFullText(source.access);
  const organization = [source.organization, source.version ? `Version ${source.version}` : null]
    .filter(Boolean)
    .join(" · ");
  const fullHref = sourceHref(source, clause);
  const body = excerpt || source.description;

  return (
    <dialog
      ref={dialogRef}
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      className="fixed inset-0 m-0 h-full max-h-none w-full max-w-none bg-transparent p-0 text-ink backdrop:bg-black/60"
      aria-label="Source"
    >
      <aside
        className="evidence-drawer ml-auto h-full w-full max-w-[480px] overflow-y-auto border-l border-line bg-page p-6 md:p-8"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <p className="text-[11px] uppercase tracking-wide text-faint">
            Source
          </p>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close source panel"
            className="rounded-full border border-line px-3 py-1 text-sm text-muted transition-colors hover:border-line-strong hover:text-ink focus-visible:ring-2 focus-visible:ring-white/40 focus-visible:outline-none"
          >
            Close
          </button>
        </div>

        {source.category && <p className="eyebrow mt-5">{source.category}</p>}
        {source.title && <h3 className="mt-2 text-lg font-medium tracking-tight text-ink">
          {source.title}
        </h3>}
        {organization && (
          <p className="mt-2 text-sm text-muted">{organization}</p>
        )}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {source.access && <span className="pill">{accessLabel(source.access)}</span>}
          {source.isNumber && (
            <span className="pill font-mono text-chroma">{source.isNumber}</span>
          )}
        </div>

        <div className="mt-6 border-t border-line pt-6">
          <h4 className="text-[0.8125rem] font-medium text-ink">
            Relevant excerpt
          </h4>
          {metaOnly ? (
            <p className="mt-3 rounded-[var(--r-inner)] border border-line bg-surface px-4 py-3 text-sm leading-6 text-faint">
              Excerpts are not available for this document under its licence
              class. Open the source to read the original.
            </p>
          ) : body ? (
            <p className="evidence-highlight mt-3 whitespace-pre-wrap rounded-[var(--r-inner)] border border-line bg-surface px-4 py-3 text-sm leading-6 text-muted" aria-label="Retrieved evidence text">
              {body}
            </p>
          ) : (
            <p className="mt-3 text-sm text-faint">No excerpt is stored.</p>
          )}
        </div>

        <div className="mt-6 border-t border-line pt-6">
          <h4 className="text-[0.8125rem] font-medium text-ink">
            Source information
          </h4>
          <SourceMetadata
            source={source}
            clause={clause}
            page={page}
            section={section}
          />
        </div>

        {source.relatedStandards.length > 0 && (
          <div className="mt-6 border-t border-line pt-6">
            <h4 className="text-[0.8125rem] font-medium text-ink">
              Related standard
            </h4>
            <ul className="mt-4 space-y-2">
              {source.relatedStandards.map((standard) => (
                <li
                  key={standard.id || standard.is_number || "standard"}
                  className="rounded-[var(--r-inner)] border border-line bg-surface px-4 py-3"
                >
                  {standard.id ? (
                    <Link
                      href={`/standards/${standard.id}`}
                      className="text-sm transition-colors hover:text-ink"
                    >
                      {standard.is_number && (
                        <span className="font-mono text-xs text-chroma">
                          {standard.is_number}
                        </span>
                      )}
                      {standard.title && <span> {standard.title}</span>}
                    </Link>
                  ) : (
                    <span className="text-sm text-muted">
                      {standard.is_number || standard.title}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {source.id && (
          <div className="mt-6 flex flex-col gap-3 border-t border-line pt-6">
            {fullHref && (
              <Link
                className="button w-full"
                href={fullHref}
                onClick={onClose}
              >
                {clause ? `Read full source at clause ${clause} →` : "Read full source →"}
              </Link>
            )}
            {onAsk ? (
              <button
                className="button button-primary w-full"
                onClick={() => onAsk(source, { clause, page, section, excerpt })}
              >
                Ask Assistant about this source →
              </button>
            ) : (
              <Link
                className="button button-primary w-full"
                href={assistantHref(source, clause, page)}
                onClick={onClose}
              >
                Ask Assistant about this source →
              </Link>
            )}
          </div>
        )}
      </aside>
    </dialog>
  );
}
