import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


class LicenceViolationError(Exception):
    """A parser tried to hand back full text for a metadata_only source.

    Hard invariant from CLAUDE.md: metadata_only content (ISO-adopted
    standards, paywalled material, priced publications) must never be stored
    as full text.
    """


@dataclass
class SourceRecord:
    url: str
    title: str
    publisher: str
    source_type: str
    licence_class: str  # 'full_text_ok' or 'metadata_only'
    retrieved_at: datetime
    content_hash: str
    metadata: dict = field(default_factory=dict)

    @property
    def raw_bytes(self) -> bytes:
        """Raw fetched content, carried alongside the record metadata."""
        return self.metadata.get("raw_bytes", b"")


def content_hash(raw_bytes: bytes) -> str:
    """SHA-256 content hash for change detection."""
    return hashlib.sha256(raw_bytes).hexdigest()


def now_utc():
    return datetime.now(timezone.utc)


class ParserBase(ABC):
    """Abstract base for all PDF/HTML parsers."""

    @abstractmethod
    async def can_handle(self, url: str, raw_bytes: bytes) -> bool:
        """Check if this parser can handle the given content."""
        pass

    @abstractmethod
    async def parse(self, raw_bytes: bytes, url: str) -> dict:
        """Parse raw content and return structured data."""
        pass


class Hasher:
    @staticmethod
    def sha256(raw_bytes: bytes) -> str:
        return hashlib.sha256(raw_bytes).hexdigest()


_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")


class ClauseExtractor:
    """Extracts a clause hierarchy from parser output.

    Parsers that already return a ``clauses`` list (the PDF parsers) are used
    as-is. Everything else falls back to scanning the extracted text line by
    line with the shared clause-number detector in ``pdf_parser``.
    """

    def extract(
        self,
        raw_bytes: bytes,
        source_type: str,
        parsed: dict,
    ) -> list:
        clauses = parsed.get("clauses")
        if not clauses:
            metadata = parsed.get("metadata") or {}
            clauses = metadata.get("clauses")
        if clauses:
            # Parent rows must be insertable before their children; depth
            # ordering guarantees parents come first.
            return sorted(
                clauses,
                key=lambda c: (
                    len(str(c.get("path", "")).split(".")),
                    str(c.get("path", "")),
                ),
            )

        text = (
            parsed.get("full_text")
            or parsed.get("raw_text")
            or (raw_bytes.decode("utf-8", errors="replace") if raw_bytes else "")
        )
        if not text:
            return []

        from app.ingestion.parsers.pdf_parser import build_clause_tree_from_lines

        # HTML is often a single line: tags become newlines so each clause
        # heading lands at the start of its own line, where the detector
        # (which anchors at ^) can see it. Plain PDF text has no tags, so it
        # passes through unchanged.
        lines = _TAG_RE.sub("\n", text).splitlines()
        return build_clause_tree_from_lines(
            lines, document_title=parsed.get("title") or ""
        )


class Pipeline:
    """Orchestrates: content hash → parse → version → clauses → chunks → store.

    Fetching happens before this runs (the caller carries the raw bytes on
    ``SourceRecord.metadata['raw_bytes']``). LLM/RAG embedding is out of scope.
    """

    def __init__(self, db_session, clause_extractor: Optional[ClauseExtractor] = None):
        self.db = db_session
        self.clause_extractor = clause_extractor or ClauseExtractor()

    async def process(self, source: SourceRecord) -> dict:
        """Full pipeline for one source."""
        from app.models import Chunk, ClauseNode, Document, Source as DBSource
        from sqlalchemy import func, select

        if source.licence_class not in ("full_text_ok", "metadata_only"):
            raise LicenceViolationError(
                f"unknown licence_class {source.licence_class!r} for {source.url}"
            )

        raw_bytes = source.raw_bytes
        incoming_hash = source.content_hash or content_hash(raw_bytes)

        # Parse first: the parsed licence claim is checked against the
        # source's assigned licence class below.
        parsed = await self._parse(source.source_type, raw_bytes, source.url)
        self._check_licence(source, parsed)

        # Locate the source row and decide the document version.
        existing = (
            await self.db.execute(
                select(DBSource).where(DBSource.url == source.url)
            )
        ).scalar_one_or_none()

        version = 1
        if existing is not None:
            latest = (
                await self.db.execute(
                    select(Document.version, Document.content_hash)
                    .where(Document.source_id == existing.id)
                    .order_by(Document.version.desc())
                    .limit(1)
                )
            ).first()
            if latest and latest.content_hash == incoming_hash:
                # Same content already ingested — nothing to do.
                return {"status": "skipped", "reason": "unchanged"}
            version = ((await self.db.execute(
                select(func.max(Document.version)).where(
                    Document.source_id == existing.id
                )
            )).scalar() or 0) + 1

            # CLAUDE.md: source rows are immutable once created. Changed
            # content lands on a new Document version; the source row stands.
            db_source = existing
        else:
            db_source = DBSource(
                url=source.url,
                title=source.title,
                publisher=source.publisher,
                source_type=source.source_type,
                licence_class=source.licence_class,
                retrieved_at=source.retrieved_at,
                content_hash=incoming_hash,
                meta={
                    k: v
                    for k, v in (source.metadata or {}).items()
                    if k != "raw_bytes"
                },
            )
            self.db.add(db_source)
            await self.db.flush()

        # Document version. Full text is only stored for full_text_ok sources
        # (metadata_only raw text never reaches the database).
        if source.licence_class == "full_text_ok":
            raw_text = (
                parsed.get("full_text")
                or raw_bytes.decode("utf-8", errors="replace")
            )
        else:
            raw_text = None

        doc = Document(
            source_id=db_source.id,
            version=version,
            content_hash=incoming_hash,
            is_number=parsed.get("is_number"),
            title=parsed.get("title") or source.title,
            raw_text=raw_text,
            licence_class=source.licence_class,
            parsed_at=now_utc(),
        )
        self.db.add(doc)
        await self.db.flush()

        # Clause extraction → clause persistence (parents flushed before
        # children) → chunk generation → chunk persistence.
        clauses = self.clause_extractor.extract(
            raw_bytes, source.source_type, parsed
        )
        node_ids = await self._persist_clauses(doc, clauses)
        chunks = self._build_chunks(
            doc, clauses, node_ids, source.licence_class, parsed
        )
        for chunk in chunks:
            self.db.add(chunk)

        await self.db.commit()

        return {
            "status": "completed",
            "version": version,
            "document_id": str(doc.id),
            "chunks_created": len(chunks),
            "clauses_extracted": len(clauses),
        }

    async def _persist_clauses(self, document, clauses: list) -> dict:
        """Persist ClauseNodes for one document, before any chunk references
        them. Returns {clause_path: node id}."""
        from app.models import ClauseNode

        nodes_by_path: dict = {}
        # Sorted parents-first (see ClauseExtractor.extract); flush per node
        # so parent ids exist before children reference them.
        for order_index, clause in enumerate(clauses):
            path = str(clause.get("path"))
            if not path:
                continue
            parent_path = ".".join(path.split(".")[:-1]) if "." in path else None
            parent = nodes_by_path.get(parent_path) if parent_path else None
            node = ClauseNode(
                document_id=document.id,
                clause_path=path[:50],
                parent_id=parent.id if parent else None,
                title=(clause.get("title") or "")[:500] or None,
                depth=len(path.split(".")),
                order_index=order_index,
            )
            self.db.add(node)
            await self.db.flush()
            nodes_by_path[path] = node
        return {path: node.id for path, node in nodes_by_path.items()}

    async def _parse(self, source_type: str, raw_bytes: bytes, url: str) -> dict:
        """Dispatch to the registered parser for this source type."""
        from app.ingestion.parsers import get_parser

        parser_cls = get_parser(source_type)
        if parser_cls is None:
            return {"title": url.rsplit("/", 1)[-1] or url, "is_number": None}
        parser = parser_cls()
        return await parser.parse(raw_bytes, url)

    def _build_chunks(
        self,
        document,
        clauses: list,
        node_ids: dict,
        licence_class: str,
        parsed: dict,
    ) -> list:
        """Build flat chunks from the persisted clause tree."""
        from app.models import Chunk

        chunks = []
        for i, clause in enumerate(clauses):
            path = clause.get("path")
            if licence_class == "full_text_ok":
                text = clause.get("text") or clause.get("title") or ""
            else:
                # metadata_only: clause headings are structure, not body
                # text — the body is never persisted (CLAUDE.md).
                text = clause.get("title") or ""
            text = text or str(path or "")

            chunks.append(
                Chunk(
                    document_id=document.id,
                    clause_node_id=node_ids.get(str(path)),
                    chunk_index=i,
                    text=text,
                    token_count=len(text.split()),
                    meta={
                        "clause_path": path,
                        "depth": len(str(path).split(".")) if path else 0,
                    },
                )
            )

        # Fallback chunk when no clause structure was found.
        if not chunks:
            if licence_class == "full_text_ok":
                text = (document.raw_text or "")[:1000]
            else:
                text = ""
            text = text or document.title or parsed.get("title") or ""
            chunks.append(
                Chunk(
                    document_id=document.id,
                    chunk_index=0,
                    text=text,
                    token_count=len(text.split()),
                    meta={"clause_path": None, "depth": 0},
                )
            )

        return chunks

    @staticmethod
    def _check_licence(source: SourceRecord, parsed: dict) -> None:
        """Enforce the CLAUDE.md licence invariant before anything is stored."""
        if (
            source.licence_class == "metadata_only"
            and parsed.get("licence_class") == "full_text_ok"
        ):
            raise LicenceViolationError(
                f"parser claims full_text_ok for metadata_only source {source.url}"
            )
