import type { StandardSummary } from "@/lib/api";

function StatusBadge({ status }: { status: string }) {
  const color = status === "current" ? "text-chroma" : "text-faint";
  return (
    <span className={`text-[11px] ${color}`}>
      {status}
    </span>
  );
}

export function StandardsList({
  results,
  onSelect,
}: {
  results: StandardSummary[];
  onSelect: (id: string) => void;
}) {
  if (results.length === 0) return null;
  return (
    <ul className="result-list mt-4 space-y-3">
      {results.map((std) => (
        <li key={std.id} className="result-reveal">
          <button
            type="button"
            onClick={() => onSelect(std.id)}
            aria-label={`Open ${std.title}`}
            className="interactive-card w-full rounded-[var(--r-card)] border border-line bg-surface p-6 text-left hover:border-line-strong hover:bg-elevated focus-visible:ring-2 focus-visible:ring-white/40 focus-visible:outline-none"
          >
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <span className="font-mono text-[0.875rem] text-ink">
                {std.is_number}
              </span>
              <StatusBadge status={std.status} />
              {std.sector && (
                <span className="text-[11px] text-faint">{std.sector}</span>
              )}
              {std.year && (
                <span className="text-[11px] text-faint">{std.year}</span>
              )}
            </div>
            <h3 className="mt-2 text-lg font-medium leading-7 tracking-tight text-ink">
              {std.title}
            </h3>
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-faint">
              <span>{std.document_count} document{std.document_count === 1 ? "" : "s"}</span>
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}
