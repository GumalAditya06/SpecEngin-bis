import Link from "next/link";
import { accessLabel, DESCRIPTION_FALLBACK } from "@/lib/sources/records";
import type { SourceRecord } from "@/lib/sources/types";

/**
 * The single source card used by the library, Standards, Services and
 * Laboratories. `action="link"` navigates to the source detail page;
 * `action="drawer"` opens the shared SourceDrawer so the surrounding page keeps
 * its context.
 */
export default function SourceCard({
  source,
  href,
  action = "link",
  onOpen,
}: {
  source: SourceRecord;
  href?: string;
  action?: "link" | "drawer";
  onOpen?: (source: SourceRecord) => void;
}) {
  const body = (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="eyebrow">{source.category}</p>
        <span className="pill">{accessLabel(source.access)}</span>
      </div>
      <h2 className="mt-3 text-lg font-medium">{source.title}</h2>
      <p className="mt-2 line-clamp-2 text-sm leading-7 text-muted">
        {source.description || DESCRIPTION_FALLBACK}
      </p>
      <div className="mt-5 flex flex-wrap justify-between gap-3 text-xs text-faint">
        <span>
          {[source.organization, source.version ? `Version ${source.version}` : null]
            .filter(Boolean)
            .join(" · ")}
        </span>
        <span className="text-ink">
          {action === "drawer" ? "Inspect source →" : "Read document →"}
        </span>
      </div>
    </>
  );
  const shell =
    "panel interactive-card block hover:border-line-strong focus-visible:ring-2 focus-visible:ring-white/40 focus-visible:outline-none";
  if (action === "drawer" && onOpen) {
    return (
      <button
        type="button"
        className={`${shell} text-left`}
        onClick={() => onOpen(source)}
        aria-label={`Inspect source: ${source.title}`}
      >
        {body}
      </button>
    );
  }
  return (
    <Link href={href || `/sources/${source.id}`} className={shell}>
      {body}
    </Link>
  );
}
