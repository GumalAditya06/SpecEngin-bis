import type { Stats } from "@/lib/api";

export function StatsRow({ stats }: { stats: Stats | null }) {
  if (!stats) {
    return (
      <div className="mt-8 grid gap-4 md:grid-cols-4">
        {[0, 0, 0, 0].map((i) => (
          <div key={i} className="rounded-[var(--r-card)] border border-line bg-surface p-4">
            <div className="h-4 w-16 rounded bg-line opacity-30" />
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="mt-8 grid gap-4 md:grid-cols-6">
      {[
        { label: "Sources", value: stats.sources },
        { label: "Documents", value: stats.documents },
        { label: "Clauses", value: stats.clause_nodes },
        { label: "Chunks", value: stats.chunks },
        { label: "Standards", value: stats.standards },
        { label: "Labs", value: stats.laboratories },
      ].map((item) => (
        <div key={item.label} className="rounded-[var(--r-card)] border border-line bg-surface p-4">
          <div className="text-[0.8125rem] text-faint">{item.label}</div>
          <div className="mt-1 text-2xl font-medium text-ink">{item.value}</div>
        </div>
      ))}
    </div>
  );
}
