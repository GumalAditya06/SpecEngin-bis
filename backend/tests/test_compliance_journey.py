"""End-to-end tests for the manufacturer compliance journey.

Covers the demo scenario: "I manufacture an LED television in India" —
product attribute extraction, standard mapping, the certification route,
laboratory lookup, and compliance case creation. Runs on SQLite.
"""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.db import async_session_local, engine
from app.models import Base
from app.models.base import (
    Laboratory,
    Source,
    Standard,
    Test,
    standard_laboratory,
)
from app.rag.embeddings import embed_text
from app.rag.pipeline import run_pipeline
from app.rag.product_mapper import extract_attributes, map_product_to_standards


@pytest.fixture(autouse=True)
async def _tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


def _make_source(url, title, publisher, source_type, licence_class, meta=None):
    return dict(
        url=url,
        title=title,
        publisher=publisher,
        source_type=source_type,
        licence_class=licence_class,
        retrieved_at=datetime.now(timezone.utc),
        content_hash="a" * 64,
        meta=meta or {},
    )


@pytest.fixture
async def seeded_tv_db():
    """Seed a TV standard + a lab with tests + a QCO document.

    Idempotent: the conftest DB persists across tests in a run, so an
    existing IS 616:2017 row means everything is already seeded.
    """
    async with async_session_local() as db:
        existing = (
            await db.execute(
                select(Standard).where(Standard.is_number == "IS 616:2017")
            )
        ).scalar_one_or_none()
        if existing:
            return "IS 616:2017"

        std = Standard(
            is_number="IS 616:2017",
            title="Audio, video and similar electronic apparatus — Safety requirements",
            sector="Electronics",
            status="current",
            year=2017,
            description="Safety requirements for electronic apparatus including television receivers.",
        )
        db.add(std)
        await db.flush()

        lab = Laboratory(
            name="Electronic Test Laboratory",
            city="Bengaluru",
            state="Karnataka",
            recognition_status="recognized",
            address="Whitefield, Bengaluru",
        )
        db.add(lab)
        await db.flush()

        db.add(
            Test(
                laboratory_id=lab.id,
                standard_id=std.id,
                test_name="Safety test",
            )
        )
        db.add(
            Test(
                laboratory_id=lab.id,
                standard_id=std.id,
                test_name="Marking inspection",
            )
        )
        await db.execute(
            standard_laboratory.insert().values(
                standard_id=std.id, laboratory_id=lab.id
            )
        )

        src = Source(
            **_make_source(
                "https://example.com/is-616.pdf",
                "IS 616:2017 Electronic apparatus safety",
                "Bureau of Indian Standards",
                "indigenous_pdf",
                "full_text_ok",
                meta={"sector": "Electronics"},
            )
        )
        db.add(src)
        await db.flush()
        from app.models.base import Chunk, Document

        doc = Document(
            source_id=src.id,
            standard_id=std.id,
            version=1,
            content_hash="a" * 64,
            is_number="IS 616:2017",
            title="Audio, video and similar electronic apparatus — Safety requirements",
            raw_text="Television receivers shall meet the safety requirements.",
            parsed_at=datetime.now(timezone.utc),
            licence_class="full_text_ok",
        )
        db.add(doc)
        await db.flush()
        # Three clauses, mirroring the real seed shape (scope + marking +
        # tests) so the citation count supports STRONG_EVIDENCE/VERIFIED.
        chunks = [
            (
                "1",
                "Television receivers shall meet the safety requirements for electronic apparatus.",
            ),
            (
                "5",
                "Each television receiver shall be marked with the IS number and the manufacturer's name.",
            ),
            (
                "4",
                "The receiver shall be subjected to the safety test and the marking inspection.",
            ),
        ]
        for i, (path, text) in enumerate(chunks):
            db.add(
                Chunk(
                    document_id=doc.id,
                    chunk_index=i,
                    text=text,
                    token_count=len(text.split()),
                    meta={"clause_path": path, "section": "", "depth": 1},
                    search_vector=text,
                    embedding=embed_text(text),
                )
            )
        await db.commit()
    return "IS 616:2017"


# ------------------------------------------------------ product mapper


def test_extract_attributes_led_tv():
    attrs = extract_attributes("I manufacture an LED television in India")
    assert {"attribute": "sector_hint", "value": "Electronics"} in attrs


@pytest.mark.asyncio
async def test_map_led_tv_with_db(seeded_tv_db):
    async with async_session_local() as db:
        mapping = await map_product_to_standards(
            db, "I manufacture an LED television in India"
        )
        assert not mapping["ambiguous"]
        assert any(
            c["is_number"] == "IS 616:2017" for c in mapping["candidates"]
        )


# ------------------------------------------------------ pipeline journey


@pytest.mark.asyncio
async def test_pipeline_full_journey(seeded_tv_db):
    async with async_session_local() as db:
        result = await run_pipeline(
            db,
            "I manufacture an LED television in India. What BIS requirements do I need to meet?",
        )
        # Product understanding surfaced
        assert result["attributes"]
        # Standards matched
        assert any(
            s["is_number"] == "IS 616:2017" for s in result["standards"]
        )
        # Certification route present (DB-driven, only with matched standards)
        assert result["route"]
        assert result["route"][0]["step"] == 1
        # Laboratories linked with tests
        assert result["laboratories"]
        labs = result["laboratories"]
        assert labs[0]["name"] == "Electronic Test Laboratory"
        assert "Safety test" in labs[0]["tests"]
        # Evidence + citations still grounded
        assert result["citations"]
        assert result["confidence_state"] in ("VERIFIED", "STRONG_EVIDENCE")


@pytest.mark.asyncio
async def test_pipeline_unmatched_product_no_route(seeded_tv_db):
    """A product with no evidence must not get an invented journey."""
    async with async_session_local() as db:
        result = await run_pipeline(
            db, "I manufacture wubble frobnicators in India"
        )
        # No mapped product and only weak evidence (if any) → the response
        # must not fabricate a journey: no route, no laboratories, and a
        # push toward clarifying instead of a confident answer.
        assert result["confidence_state"] in (
            "NEEDS_CLARIFICATION",
            "INSUFFICIENT_EVIDENCE",
        )
        assert len(result["citations"]) < 2  # weak evidence at most
        assert result["route"] == []
        assert result["laboratories"] == []
        assert result["clarification_question"]
        assert result["next_steps"]


@pytest.mark.asyncio
async def test_pipeline_weak_match_asks_for_clarification(seeded_tv_db):
    """A vague product sentence that weakly matches must ask, not guess."""
    async with async_session_local() as db:
        result = await run_pipeline(db, "I manufacture it in India")
        # Weak single-citation match → low confidence, no product candidates,
        # so the pipeline asks the user to specify the product.
        assert result["confidence_state"] == "NEEDS_CLARIFICATION"
        assert result["clarification_question"]


# ------------------------------------------------------ compliance cases


@pytest.mark.asyncio
async def test_compliance_case_lifecycle(seeded_tv_db):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        # Get the standard id from the API
        resp = client.get("/api/v1/standards", params={"q": "IS 616"})
        assert resp.status_code == 200
        stds = resp.json()["results"]
        assert stds
        std_id = stds[0]["id"]

        # Create
        resp = client.post(
            "/api/v1/compliance-cases",
            json={
                "product": "LED television",
                "standard_ids": [std_id],
                "query": "I manufacture an LED television in India",
            },
        )
        assert resp.status_code == 200, resp.text
        case = resp.json()
        assert case["status"] == "open"
        assert case["standard_ids"] == [std_id]

        # List
        resp = client.get("/api/v1/compliance-cases")
        assert resp.status_code == 200
        listing = resp.json()
        assert listing["total"] >= 1
        found = [c for c in listing["cases"] if c["id"] == case["id"]]
        assert found and found[0]["product"] == "LED television"

        # Update status
        resp = client.patch(
            f"/api/v1/compliance-cases/{case['id']}", json={"status": "in_progress"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

        # Detail
        resp = client.get(f"/api/v1/compliance-cases/{case['id']}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["standards"][0]["is_number"] == "IS 616:2017"

        # Invalid status rejected
        resp = client.patch(
            f"/api/v1/compliance-cases/{case['id']}", json={"status": "bogus"}
        )
        assert resp.status_code == 422


# ------------------------------------------------------ SSE streaming


@pytest.mark.asyncio
async def test_stream_emits_stages_then_result(seeded_tv_db):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/v1/assistant/query/stream",
            json={"query": "I manufacture an LED television in India"},
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

            events = []
            buffer = []
            for line in resp.iter_lines():
                if line == "" and buffer:
                    events.append("\n".join(buffer))
                    buffer = []
                elif line:
                    buffer.append(line)
            if buffer:
                events.append("\n".join(buffer))

        stages = []
        result_payload = None
        for ev in events:
            for ln in ev.splitlines():
                if ln.startswith("event: result"):
                    result_payload = json.loads(
                        next(l for l in ev.splitlines() if l.startswith("data: "))[6:]
                    )
                elif ln.startswith("data: "):
                    data = json.loads(ln[6:])
                    if "stage" in data:
                        stages.append(data["stage"])

        # Journey stages present, in order
        assert stages[:1] == ["understanding"]
        assert "standards" in stages
        assert "certification" in stages
        assert "laboratories" in stages
        assert "evidence" in stages
        # Final result identical shape to non-streaming endpoint
        assert result_payload is not None
        assert result_payload["route"]
        assert result_payload["laboratories"]
        assert result_payload["answer"]
