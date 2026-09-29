// Frontend abstraction over the source/document layer.
//
// The backend splits provenance (`sources`) from the parsed artefact
// (`documents`). The product talks about one thing — "Sources" — so every
// surface (library, detail page, drawer) reads a single `SourceRecord` instead
// of the raw payload shapes. Nothing here invents data: a field is only
// populated from what an existing endpoint already returns, and stays
// `null`/`undefined` when the backend does not provide it.

export interface RelatedStandardRef {
  id: string | null;
  is_number: string | null;
  title: string | null;
}

/** A Specengine entity that relies on a source or one of its clauses. */
export interface SourceReference {
  kind: "answer" | "standard" | "service" | "laboratory";
  id: string;
  label: string;
}

export interface SourceRecord {
  /** Document id — the `/sources/[id]` key. Null for illustrative placeholders. */
  id: string | null;
  /** Source id — the provenance record the document was parsed from, when known. */
  sourceId: string | null;
  title: string;
  /** Source type label, e.g. "Indian Standard". */
  category: string;
  /** Publisher of the original record, e.g. "Bureau of Indian Standards". */
  organization: string;
  version: string | number | null;
  /** `licence_class` of the underlying source record. */
  access: string;
  isNumber: string | null;
  description: string | null;
  url: string | null;
  retrievedAt: string | null;
  /** Notification or effective date recorded in the source metadata. */
  publishedOn: string | null;
  sector: string | null;
  clauseCount: number | null;
  /** Edition year parsed from the IS number. See `editionYear`. */
  year: number | null;
  relatedStandards: RelatedStandardRef[];
}

export interface SourceClause {
  /** Anchor index — clause anchors are `clause-<index>`. */
  index: number;
  /** Clause number as recorded by the parser, e.g. "4.2". */
  number: string | null;
  title: string | null;
  /** Clause text. `null` for metadata-only sources, by licence policy. */
  text: string | null;
  depth: number;
  /**
   * Specengine references for this clause.
   * `undefined` means the backend cannot say — the section stays hidden
   * instead of being invented. An empty array means the relationship is known
   * and currently unused.
   */
  referencedBy?: SourceReference[];
}

export interface SourceClauseNode {
  number: string | null;
  title: string | null;
  depth: number;
  /** Index of the extracted clause text, or null when no text exists. */
  anchor: number | null;
  children: SourceClauseNode[];
}

/** A source plus everything needed to read it (source detail page only). */
export interface SourceReading extends SourceRecord {
  clauses: SourceClause[];
  clauseTree: SourceClauseNode[];
  fullText: string | null;
  /** Document-level Specengine references. `undefined` = not knowable yet. */
  referencedBy?: SourceReference[];
}
