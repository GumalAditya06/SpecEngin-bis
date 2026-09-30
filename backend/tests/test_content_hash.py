"""Content hashing — the basis for source change detection."""

import hashlib

from app.ingestion.pipeline import Hasher, SourceRecord, content_hash


def test_hash_matches_stdlib_sha256():
    payload = b"IS 8921:2024 steel wire ropes"
    assert content_hash(payload) == hashlib.sha256(payload).hexdigest()


def test_hash_is_stable_across_calls():
    payload = b"stable content"
    assert content_hash(payload) == content_hash(payload)
    assert Hasher.sha256(payload) == content_hash(payload)


def test_hash_changes_when_content_changes():
    base = b"<html>revision 1</html>"
    changed = b"<html>revision 2</html>"
    assert content_hash(base) != content_hash(changed)


def test_hash_is_64_char_hex():
    digest = content_hash(b"")
    assert len(digest) == 64
    int(digest, 16)  # raises if not hex


def test_source_record_carries_raw_bytes_and_metadata():
    rec = SourceRecord(
        url="https://example.org/a",
        title="t",
        publisher="p",
        source_type="qco",
        licence_class="full_text_ok",
        retrieved_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
        content_hash=content_hash(b"abc"),
        metadata={"raw_bytes": b"abc"},
    )
    assert rec.raw_bytes == b"abc"


def test_source_record_metadata_default_is_not_shared():
    """Regression: dataclass mutable defaults must not be shared."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    def rec():
        return SourceRecord(
            url="u",
            title="t",
            publisher="p",
            source_type="qco",
            licence_class="full_text_ok",
            retrieved_at=now,
            content_hash="h",
        )

    a, b = rec(), rec()
    a.metadata["raw_bytes"] = b"x"
    assert "raw_bytes" not in b.metadata
