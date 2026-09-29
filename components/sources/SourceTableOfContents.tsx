import type { SourceClauseNode } from "@/lib/sources/types";

function ClauseLink({
  node,
  active,
  onSelect,
}: {
  node: SourceClauseNode;
  active: number | null;
  onSelect: (anchor: number) => void;
}) {
  const current = active === node.anchor;
  const label = (
    <>
      <span className="shrink-0 font-mono text-[11px] text-chroma">
        {node.number ? `§ ${node.number}` : "§"}
      </span>
      <span className="min-w-0 text-muted transition-colors group-hover:text-ink">
        {node.title || "Source excerpt"}
      </span>
    </>
  );
  return (
    <li>
      {node.anchor !== null ? (
        <a
          href={`#clause-${node.anchor}`}
          onClick={() => onSelect(node.anchor as number)}
          aria-current={current ? "location" : undefined}
          className={`group flex gap-2 rounded-[var(--r-inner)] px-2 py-1.5 text-xs leading-6 transition-colors hover:bg-surface ${
            current ? "bg-surface text-ink" : ""
          }`}
        >
          {label}
        </a>
      ) : (
        <span className="flex gap-2 px-2 py-1.5 text-xs leading-6">{label}</span>
      )}
      {node.children.length > 0 && (
        <ul className="mt-1 space-y-1 border-l border-line pl-3">
          {node.children.map((child, index) => (
            <ClauseLink
              key={`${child.number ?? "clause"}-${index}`}
              node={child}
              active={active}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

/**
 * Table of contents for a source. Clause numbers and titles come from the
 * clause tree the backend extracted; a clause with no stored text stays
 * visible as a label so the document outline is never faked.
 */
export default function SourceTableOfContents({
  nodes,
  active = null,
  onSelect,
  label = "Source clauses",
}: {
  nodes: SourceClauseNode[];
  active?: number | null;
  onSelect?: (anchor: number) => void;
  label?: string;
}) {
  if (!nodes.length)
    return (
      <p className="mt-4 text-sm leading-7 text-muted">
        No clause structure has been extracted for this source.
      </p>
    );
  return (
    <nav aria-label={label} className="mt-4">
      <ul className="space-y-1">
        {nodes.map((node, index) => (
          <ClauseLink
            key={`${node.number ?? "clause"}-${index}`}
            node={node}
            active={active}
            onSelect={onSelect ?? (() => {})}
          />
        ))}
      </ul>
    </nav>
  );
}
