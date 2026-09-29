import SourceCard from "@/components/sources/SourceCard";
import type { SourceRecord } from "@/lib/sources/types";

/**
 * Compact source list for pages that reference a source rather than own it.
 * The cards are the shared SourceCard; only the trigger differs.
 */
export default function SourceList({
  sources,
  onOpen,
  empty = "No source documents are linked yet.",
}: {
  sources: SourceRecord[];
  onOpen: (source: SourceRecord) => void;
  empty?: string;
}) {
  if (!sources.length)
    return <p className="text-sm leading-7 text-muted">{empty}</p>;
  return (
    <ul className="result-list grid gap-3">
      {sources.map((source) => (
        <li key={source.id ?? source.title} className="result-reveal">
          <SourceCard source={source} action="drawer" onOpen={onOpen} />
        </li>
      ))}
    </ul>
  );
}
