"""Shared test fixtures.

The environment is pinned to a throwaway SQLite database *before* any app
module is imported, so tests can never touch a real Postgres instance.
"""

import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="specengine-bis-tests-")
os.environ["BIS_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DIR}/app.db"
os.environ.setdefault("BIS_SECRET_KEY", "test-secret")

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ingestion.pipeline import SourceRecord, content_hash, now_utc
from app.models import Base


@pytest.fixture
async def db(tmp_path):
    """A fresh async session on an isolated SQLite file, tables created."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ingest.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def make_pdf_bytes(lines: list[str]) -> bytes:
    """Build a minimal, valid single-page PDF containing ``lines`` of text.

    Hand-assembled (with correct xref offsets) so tests exercise the real
    pdfplumber extraction path without needing a PDF-generation library.
    """
    content_parts = ["BT", "/F1 11 Tf", "50 740 Td"]
    for i, line in enumerate(lines):
        if i:
            content_parts.append("0 -16 Td")
        escaped = (
            line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        )
        content_parts.append(f"({escaped}) Tj")
    content_parts.append("ET")
    stream = "\n".join(content_parts).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


SAMPLE_PDF_LINES = [
    "IS 12345 : 2024 Indian Standard Specification for Steel Wire",
    "5.1 Scope",
    "This standard covers steel wire for general engineering purposes.",
    "5.2 Material",
    "5.2.1 Grade A",
    "Wire shall be bright or galvanised as specified.",
]

SAMPLE_QCO_HTML = (
    "<html><head><title>Steel and Steel Products QCO 2025</title></head>"
    "<body><h1>Steel and Steel Products (Quality Control) Order, 2025</h1>"
    "<table><tr><th>Product</th></tr>"
    "<tr><td>Steel wire ropes</td></tr></table>"
    "<p>3.1 Restriction on manufacture of steel products</p>"
    "<p>Steel shall not be manufactured unless it conforms to IS 8921:2024.</p>"
    "<p>3.1.1 Storage of finished product</p>"
    "<p>Finished products shall be stored under cover, off the ground.</p>"
    "<p>3.2 Conformity and marking requirements</p>"
    "<p>Every product shall bear the Standard Mark under a licence.</p>"
    "<p>5.1 Enforcement of this Order</p>"
    "<p>The Bureau may take samples and cause them to be tested.</p>"
    "</body></html>"
).encode("utf-8")


def make_source_record(
    raw_bytes: bytes,
    url: str = "https://www.bis.gov.in/qco/steel-steel-products-order-2025",
    source_type: str = "qco",
    licence_class: str = "full_text_ok",
    **overrides,
) -> SourceRecord:
    """SourceRecord carrying fetched bytes, exactly as the fetch stage would."""
    kwargs = dict(
        url=url,
        title=overrides.pop("title", "Steel and Steel Products QCO 2025"),
        publisher=overrides.pop("publisher", "Ministry of Steel"),
        source_type=source_type,
        licence_class=licence_class,
        retrieved_at=overrides.pop("retrieved_at", now_utc()),
        content_hash=overrides.pop("content_hash", content_hash(raw_bytes)),
        metadata={"raw_bytes": raw_bytes},
    )
    kwargs.update(overrides)
    return SourceRecord(**kwargs)
