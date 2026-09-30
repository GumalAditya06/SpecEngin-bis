"""Seed the BIS standards database with realistic sample data.

Licence classes follow the hard-coded rules in CLAUDE.md:
- indigenous_pdf / qco / faq_pages / lab_lists  -> full_text_ok
- scheme_reg / hallmarking_docs / kys_catalogue -> metadata_only (no raw_text)

Run:  .venv/bin/python -m app.seed
"""

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.db import async_session_local, engine
from app.rag.embeddings import embed_text
from app.models.base import (
    Base,
    Amendment,
    CertificationScheme,
    Chunk,
    ClauseNode,
    Document,
    Laboratory,
    ProductCategory,
    Source,
    Standard,
    Test,
    standard_laboratory,
    standard_related,
)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def now_utc():
    return datetime.now(timezone.utc)


# (source record kwargs, document, clauses [path,title,text])
# text may be omitted for metadata_only sources (full text is never stored).
SEED = [
    dict(
        source=dict(
            url="https://standards.bis.gov.in/IS_8921_2024.pdf",
            title="IS 8921:2024 Steel wire ropes for general engineering purposes",
            publisher="Bureau of Indian Standards",
            source_type="indigenous_pdf",
            licence_class="full_text_ok",
            meta={"sector": "Metallurgy", "status": "Current"},
        ),
        document=dict(
            is_number="IS 8921:2024",
            title="Steel wire ropes for general engineering purposes",
        ),
        clauses=[
            ("1", "Scope", "This standard covers steels wire ropes for general engineering purposes such as hoisting, hauling and towing. It specifies the materials, construction, breaking loads and marking requirements."),
            ("2", "Normative references", "The standards listed in this clause are necessary adjuncts to this standard. IS 1835 specifies the method for tensile testing of steel wire."),
            ("3", "Terms and definitions", "For the purpose of this standard, the following definitions apply. Core: the inner part of a fibre or steel core. Lay: the helical disposition of wires in a strand."),
            ("4", "Materials", "Steel shall be of a quality adapted to the class of rope. Wire shall be bright or galvanised as specified, free from harmful defects."),
            ("5", "Construction", "The number and arrangement of strands and wires shall be as given in the tables. Each strand shall be formed of wires laid helically around the core."),
            ("6", "Breaking load", "The minimum breaking load of a rope shall be computed from the sum of the breaking loads of individual wires multiplied by a spinning factor."),
            ("7", "Marking", "Every rope shall be marked with the IS number, manufacturer's name or trade mark, and the class of rope."),
        ],
    ),
    dict(
        source=dict(
            url="https://standards.bis.gov.in/IS_12034_2025.pdf",
            title="IS 12034:2025 Portable fire extinguishers — water type",
            publisher="Bureau of Indian Standards",
            source_type="indigenous_pdf",
            licence_class="full_text_ok",
            meta={"sector": "Fire Safety", "status": "Current"},
        ),
        document=dict(
            is_number="IS 12034:2025",
            title="Portable fire extinguishers, water type",
        ),
        clauses=[
            ("1", "Scope", "This standard specifies the requirements and methods of test for portable fire extinguishers of the water type for fighting Class A fires."),
            ("2", "Terms and definitions", "Charge: the content of the extinguisher. Discharge time: the time taken for the discharge of the full charge under specified conditions."),
            ("3", "Materials", "The shell shall be of mild steel sheet, seamless drawn or welded, having a minimum thickness as given in Table 1."),
            ("4", "Tests", "The extinguisher shall be subjected to a proof pressure test, a freezing test and a discharge test."),
            ("5", "Marking", "Each extinguisher shall carry the IS number, the nominal water capacity in litres, and the operating instructions."),
        ],
    ),
    dict(
        source=dict(
            url="https://www.bis.gov.in/qco/steel-and-steel-products-order-2025",
            title="Steel and Steel Products (Quality Control) Order, 2025",
            publisher="Ministry of Steel, Government of India",
            source_type="qco",
            licence_class="full_text_ok",
            meta={
                "department": "Ministry of Steel",
                "product": "Steel wire ropes",
                "is_number": "IS 8921:2024",
                "notified_on": "18 August 2025",
                "effective_on": "15 February 2026",
                "sector": "Metallurgy",
            },
        ),
        document=dict(
            is_number=None,
            title="Steel and Steel Products (Quality Control) Order, 2025",
        ),
        clauses=[
            ("1", "Short title and commencement", "The Order may be called the Steel and Steel Products (Quality Control) Order, 2025. It shall come into force on the 15th day of February, 2026."),
            ("2", "Definitions", "In this Order, 'standard' means the relevant Indian Standard. 'Schedule' means a Schedule annexed to this Order."),
            ("3", "Restriction on manufacture", "No person shall manufacture or store specified steel products for the purpose of sale unless they conform to the relevant Indian Standard and bear the Standard Mark."),
            ("4", "Enforcement", "The Bureau of Indian Standards may, for the purpose of ensuring conformity, take samples and cause them to be tested."),
            ("5", "Offences and penalties", "Any person who contravenes the provisions of this Order shall be punishable in accordance with Section BR of the Bureau of Indian Standards Act."),
        ],
    ),
    dict(
        source=dict(
            url="https://www.bis.gov.in/qco/domestic-water-heaters-order-2025",
            title="Quality Control Order for Domestic Water Heaters, 2025",
            publisher="Department for Promotion of Industry and Internal Trade",
            source_type="qco",
            licence_class="full_text_ok",
            meta={
                "department": "DPIIT",
                "product": "Electric immersion water heaters",
                "is_number": "IS 5509:2023",
                "notified_on": "5 June 2025",
                "effective_on": "5 December 2025",
                "sector": "Electrical Appliances",
            },
        ),
        document=dict(
            is_number=None,
            title="Quality Control Order for Domestic Water Heaters, 2025",
        ),
        clauses=[
            ("1", "Short title and commencement", "This Order may be called the Quality Control Order for Domestic Water Heaters, 2025, and shall come into force on the date specified in the notification."),
            ("2", "Application", "The provisions of this Order shall apply to electric immersion water heaters covered under IS 5509."),
            ("3", "Conformity requirement", "Domestic water heaters shall bear the Standard Mark under a licence from the Bureau of Indian Standards, conforming to IS 5509:2023."),
            ("4", "Product responsibility", "The manufacturer shall be responsible for conformity of the product with the applicable standard at all times."),
        ],
    ),
    dict(
        source=dict(
            url="https://www.bis.gov.in/certification_schemes/is_8921_steel_wire_ropes",
            title="BIS Certification Scheme for Steel Wire Ropes under IS 8921:2024",
            publisher="Bureau of Indian Standards",
            source_type="scheme_reg",
            licence_class="metadata_only",
            meta={"sector": "Metallurgy", "is_number": "IS 8921:2024"},
        ),
        document=dict(
            is_number="IS 8921:2024",
            title="Certification Scheme for Steel Wire Ropes under IS 8921:2024",
        ),
        clauses=[
            ("1", "Scheme", "Conditions of certification including testing frequencies and inspection of processes to avail the Standard Mark Licence."),
            ("2", "Grant of licence", "Procedure for application, scrutiny, inspection and grant of licence."),
            ("3", "Marking", "Requirement to apply the Standard Mark and conditions governing its use."),
        ],
    ),
    dict(
        source=dict(
            url="https://www.bis.gov.in/hallmarking/manual-of-instructions-amd-3",
            title="Manual of Instructions for Recognised Hallmarking Centres, Amendment 3",
            publisher="Bureau of Indian Standards",
            source_type="hallmarking_docs",
            licence_class="metadata_only",
            meta={"sector": "Jewellery", "status": "Amendment"},
        ),
        document=dict(
            is_number=None,
            title="Manual of Instructions for Recognised Hallmarking Centres, Amendment 3",
        ),
        clauses=[
            ("1", "Amendment", "Amendment to clause 6 of the Manual concerning the use of Hallmarking Unique Identification codes."),
            ("2", "Compliance", "Updated compliance requirements for the reporting of hallmarking operations."),
        ],
    ),
    dict(
        source=dict(
            url="https://www.bis.gov.in/recognized-labs/fire-extinguishers",
            title="Recognized Test Laboratories for Fire Extinguishers",
            publisher="Bureau of Indian Standards",
            source_type="lab_lists",
            licence_class="full_text_ok",
            meta={"sector": "Fire Safety"},
        ),
        document=dict(
            is_number=None,
            title="Recognized Test Laboratories for Fire Extinguishers",
        ),
        clauses=[
            ("1", "Laboratories", "Central Fire & Rescue Services Laboratory, New Delhi. Fire Technology and Safety Laboratory, Hyderabad. Industrial Fire Laboratory, Mumbai."),
        ],
    ),
    dict(
        source=dict(
            url="https://standards.bis.gov.in/IS_616_2017.pdf",
            title="IS 616:2017 Audio, video and similar electronic apparatus — Safety requirements",
            publisher="Bureau of Indian Standards",
            source_type="indigenous_pdf",
            licence_class="full_text_ok",
            meta={"sector": "Electronics", "status": "Current"},
        ),
        document=dict(
            is_number="IS 616:2017",
            title="Audio, video and similar electronic apparatus — Safety requirements",
        ),
        clauses=[
            ("1", "Scope", "This standard covers safety requirements for audio, video and similar electronic apparatus, including television receivers and LED televisions, intended for household and similar general use."),
            ("2", "Terms and definitions", "Apparatus: a device for reproducing or receiving audio or video signals. Television receiver: apparatus for receiving and displaying broadcast television signals."),
            ("3", "General requirements", "Apparatus shall be designed and constructed so that under normal operating conditions there is no hazard to the user or the surroundings."),
            ("4", "Tests", "Apparatus shall be subjected to the electric strength test, the temperature rise test and the marking inspection."),
            ("5", "Marking", "Each television receiver shall be marked with the IS number, the manufacturer's name or trade mark, and the rated supply voltage."),
        ],
    ),
    dict(
        source=dict(
            url="https://www.bis.gov.in/qco/electronics-it-goods-order-2025",
            title="Electronics and Information Technology Goods (Requirements for Compulsory Registration) Order, 2025",
            publisher="Ministry of Electronics and Information Technology",
            source_type="qco",
            licence_class="full_text_ok",
            meta={
                "department": "MeitY",
                "product": "Television receivers",
                "is_number": "IS 616:2017",
                "notified_on": "12 March 2025",
                "effective_on": "12 September 2025",
                "sector": "Electronics",
            },
        ),
        document=dict(
            is_number=None,
            title="Electronics and Information Technology Goods (Requirements for Compulsory Registration) Order, 2025",
        ),
        clauses=[
            ("1", "Short title and commencement", "This Order may be called the Electronics and Information Technology Goods (Requirements for Compulsory Registration) Order, 2025."),
            ("2", "Application", "This Order applies to television receivers and other electronic goods specified in the Schedule, conforming to IS 616:2017."),
            ("3", "Compulsory registration", "No person shall manufacture or store for sale electronic goods specified in the Schedule unless they conform to the applicable Indian Standard and are registered with the Bureau."),
            ("4", "Self-declaration of conformity", "The manufacturer shall submit test reports from a BIS-recognized laboratory and declare conformity before registration is granted."),
            ("5", "Enforcement", "The Bureau may, for the purpose of ensuring conformity, take samples of registered goods and cause them to be tested."),
        ],
    ),
    dict(
        source=dict(
            url="https://www.bis.gov.in/recognized-labs/electronic-goods",
            title="Recognized Test Laboratories for Electronic Goods",
            publisher="Bureau of Indian Standards",
            source_type="lab_lists",
            licence_class="full_text_ok",
            meta={"sector": "Electronics"},
        ),
        document=dict(
            is_number=None,
            title="Recognized Test Laboratories for Electronic Goods",
        ),
        clauses=[
            ("1", "Laboratories", "Electronic Test Laboratory, Bengaluru. Central Electronics Testing Centre, Mohali. Regional Electronics Laboratory, Kolkata."),
        ],
    ),
    dict(
        source=dict(
            url="https://www.bis.gov.in/know-your-standard/catalogue-electrical-appliances",
            title="Know Your Standard — Electrical Appliances catalogue",
            publisher="Bureau of Indian Standards",
            source_type="kys_catalogue",
            licence_class="metadata_only",
            meta={"sector": "Electrical Appliances"},
        ),
        document=dict(
            is_number=None,
            title="Know Your Standard — Electrical Appliances catalogue",
        ),
        clauses=[
            ("1", "Catalogue", "Listing of Indian Standards applicable to domestic electrical appliances including IS 5509 (water heaters)."),
            ("2", "Search", "How to locate a standard by IS number and identify the responsible technical committee."),
        ],
    ),
]

# Standard definitions keyed by IS number.
STANDARDS = {
    "IS 8921:2024": dict(
        title="Steel wire ropes for general engineering purposes",
        sector="Metallurgy",
        status="current",
        year=2024,
        description="Steel wire ropes for general engineering purposes such as hoisting, hauling and towing.",
    ),
    "IS 12034:2025": dict(
        title="Portable fire extinguishers — water type",
        sector="Fire Safety",
        status="current",
        year=2025,
        description="Requirements and methods of test for portable fire extinguishers of the water type.",
    ),
    "IS 5509:2023": dict(
        title="Electric immersion water heaters",
        sector="Electrical Appliances",
        status="current",
        year=2023,
        description="Safety requirements for electric immersion water heaters.",
    ),
    "IS 616:2017": dict(
        title="Audio, video and similar electronic apparatus — Safety requirements",
        sector="Electronics",
        status="current",
        year=2017,
        description="Safety requirements for electronic apparatus including television receivers and LED televisions.",
    ),
}

LABORATORIES = [
    dict(name="Central Fire & Rescue Services Laboratory", city="New Delhi", state="Delhi", recognition_status="recognized", address="Sector 7, New Delhi"),
    dict(name="Fire Technology and Safety Laboratory", city="Hyderabad", state="Telangana", recognition_status="recognized", address="Hitech City, Hyderabad"),
    dict(name="Industrial Fire Laboratory", city="Mumbai", state="Maharashtra", recognition_status="recognized", address="Andheri East, Mumbai"),
    dict(name="Electronic Test Laboratory", city="Bengaluru", state="Karnataka", recognition_status="recognized", address="Whitefield, Bengaluru"),
    dict(name="Central Electronics Testing Centre", city="Mohali", state="Punjab", recognition_status="recognized", address="Sector 62, Mohali"),
    dict(name="Regional Electronics Laboratory", city="Kolkata", state="West Bengal", recognition_status="recognized", address="Salt Lake, Kolkata"),
]

LAB_STANDARD_MAP = {
    "Central Fire & Rescue Services Laboratory": ["IS 12034:2025"],
    "Fire Technology and Safety Laboratory": ["IS 12034:2025"],
    "Industrial Fire Laboratory": ["IS 12034:2025"],
    "Electronic Test Laboratory": ["IS 616:2017"],
    "Central Electronics Testing Centre": ["IS 616:2017"],
    "Regional Electronics Laboratory": ["IS 616:2017"],
}

AMENDMENTS = [
    dict(is_number="IS 8921:2024", number="A1", title="Amendment to clause 6 marking requirements", effective_year=2025),
]

CERTIFICATION_SCHEMES = [
    dict(is_number="IS 8921:2024", title="BIS Certification Scheme for Steel Wire Ropes under IS 8921:2024"),
]

PRODUCT_CATEGORIES = [
    dict(name="Steel Products", description="Steel wire ropes and related products"),
    dict(name="Fire Safety Equipment", description="Fire extinguishers and safety systems"),
    dict(name="Electrical Appliances", description="Domestic electrical appliances"),
    dict(name="Electronics", description="Television receivers and electronic goods"),
]

TESTS = [
    dict(laboratory="Central Fire & Rescue Services Laboratory", standard="IS 12034:2025", test_name="Proof pressure test"),
    dict(laboratory="Fire Technology and Safety Laboratory", standard="IS 12034:2025", test_name="Freezing test"),
    dict(laboratory="Industrial Fire Laboratory", standard="IS 12034:2025", test_name="Discharge test"),
    dict(laboratory="Electronic Test Laboratory", standard="IS 616:2017", test_name="Electric strength test"),
    dict(laboratory="Electronic Test Laboratory", standard="IS 616:2017", test_name="Temperature rise test"),
    dict(laboratory="Central Electronics Testing Centre", standard="IS 616:2017", test_name="Marking inspection"),
    dict(laboratory="Regional Electronics Laboratory", standard="IS 616:2017", test_name="Electric strength test"),
]


async def seed(clear: bool = False) -> dict:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    summary = {"created": 0, "skipped": 0, "cleared": 0}
    async with async_session_local() as db:
        if clear:
            for model in (Test, CertificationScheme, Amendment, Laboratory, Standard, Chunk, ClauseNode, Document, Source):
                await db.execute(model.__table__.delete())
            await db.commit()
            summary["cleared"] = 1

        # --- Standards ---
        standard_map: dict[str, Standard] = {}
        for is_num, data in STANDARDS.items():
            existing = (
                await db.execute(
                    select(Standard).where(Standard.is_number == is_num)
                )
            ).scalar_one_or_none()
            if existing:
                standard_map[is_num] = existing
                summary["skipped"] += 1
                continue
            std = Standard(
                is_number=is_num,
                title=data["title"],
                sector=data["sector"],
                status=data["status"],
                year=data["year"],
                description=data.get("description"),
            )
            db.add(std)
            await db.flush()
            standard_map[is_num] = std
            summary["created"] += 1

        # --- Product categories ---
        for pc in PRODUCT_CATEGORIES:
            existing = (
                await db.execute(
                    select(ProductCategory).where(ProductCategory.name == pc["name"])
                )
            ).scalar_one_or_none()
            if not existing:
                db.add(ProductCategory(name=pc["name"], description=pc.get("description")))
                await db.flush()

        # --- Laboratories ---
        lab_map: dict[str, Laboratory] = {}
        for lab in LABORATORIES:
            existing = (
                await db.execute(
                    select(Laboratory).where(Laboratory.name == lab["name"])
                )
            ).scalar_one_or_none()
            if existing:
                lab_map[lab["name"]] = existing
                continue
            l = Laboratory(
                name=lab["name"], city=lab["city"], state=lab["state"],
                recognition_status=lab["recognition_status"], address=lab["address"],
            )
            db.add(l)
            await db.flush()
            lab_map[lab["name"]] = l

        # --- Amendments ---
        for am in AMENDMENTS:
            std = standard_map.get(am["is_number"])
            if not std:
                continue
            existing = (
                await db.execute(
                    select(Amendment)
                    .where(Amendment.standard_id == std.id)
                    .where(Amendment.amendment_number == am["number"])
                )
            ).scalar_one_or_none()
            if existing:
                continue
            effective = None
            if am.get("effective_year"):
                effective = datetime(am["effective_year"], 1, 1, tzinfo=timezone.utc)
            db.add(Amendment(
                standard_id=std.id,
                amendment_number=am["number"],
                title=am["title"],
                effective_date=effective,
            ))
            await db.flush()

        # --- Certification schemes ---
        for cs in CERTIFICATION_SCHEMES:
            std = standard_map.get(cs["is_number"])
            if not std:
                continue
            existing = (
                await db.execute(
                    select(CertificationScheme)
                    .where(CertificationScheme.standard_id == std.id)
                    .where(CertificationScheme.title == cs["title"])
                )
            ).scalar_one_or_none()
            if existing:
                continue
            db.add(CertificationScheme(
                standard_id=std.id,
                title=cs["title"],
            ))
            await db.flush()

        # --- Tests ---
        for t in TESTS:
            lab = lab_map.get(t["laboratory"])
            std = standard_map.get(t["standard"])
            if not lab or not std:
                continue
            existing = (
                await db.execute(
                    select(Test)
                    .where(Test.laboratory_id == lab.id)
                    .where(Test.test_name == t["test_name"])
                )
            ).scalar_one_or_none()
            if existing:
                continue
            db.add(Test(
                laboratory_id=lab.id,
                standard_id=std.id,
                test_name=t["test_name"],
            ))
            await db.flush()

        # --- Sources / Documents ---
        for spec in SEED:
            src_kw = spec["source"]
            existing = (
                await db.execute(
                    select(Source).where(Source.url == src_kw["url"])
                )
            ).scalar_one_or_none()
            if existing:
                summary["skipped"] += 1
                continue

            raw_text = (
                "\n\n".join(c[2] for c in spec["clauses"] if len(c) > 2)
                if src_kw["licence_class"] == "full_text_ok"
                else None
            )
            raw_for_hash = (raw_text or src_kw["title"]).encode("utf-8")
            retrieved = now_utc()

            is_number = spec["document"].get("is_number")
            std = standard_map.get(is_number) if is_number else None

            source = Source(
                url=src_kw["url"],
                title=src_kw["title"],
                publisher=src_kw["publisher"],
                source_type=src_kw["source_type"],
                licence_class=src_kw["licence_class"],
                retrieved_at=retrieved,
                content_hash=sha256(raw_for_hash),
                meta=src_kw.get("meta", {}),
            )
            db.add(source)
            await db.flush()

            doc = Document(
                source_id=source.id,
                standard_id=std.id if std else None,
                version=1,
                content_hash=sha256(raw_for_hash),
                is_number=is_number,
                title=spec["document"]["title"],
                raw_text=raw_text,
                parsed_at=retrieved,
                licence_class=src_kw["licence_class"],
            )
            db.add(doc)
            await db.flush()

            nodes_by_path = {}
            for idx, (path, title, *text) in enumerate(spec["clauses"]):
                parent_path = path.rsplit(".", 1)[0] if "." in path else None
                parent_id = nodes_by_path.get(parent_path)
                node = ClauseNode(
                    document_id=doc.id,
                    clause_path=path,
                    parent_id=parent_id.id if parent_id else None,
                    title=title,
                    depth=len(path.split(".")),
                    order_index=idx,
                )
                db.add(node)
                await db.flush()
                nodes_by_path[path] = node

                chunk_text = text[0] if text else title
                db.add(
                    Chunk(
                        document_id=doc.id,
                        clause_node_id=node.id,
                        chunk_index=idx,
                        text=chunk_text,
                        token_count=len(chunk_text.split()),
                        meta={
                            "clause_path": path,
                            "depth": len(path.split(".")),
                            "section": path.rsplit(".", 1)[0] if "." in path else "",
                            "standard_number": is_number,
                            "document_title": spec["document"]["title"],
                            "source_url": src_kw["url"],
                        },
                        # search_vector is a plain Text column; on SQLite it
                        # stores raw chunk text for keyword matching fallback.
                        search_vector=chunk_text,
                        embedding=embed_text(chunk_text),
                    )
                )

            # Link lab list document to laboratories via association table
            # (Standard.laboratories / Laboratory.standards are lazy="raise",
            # so association objects are inserted directly.)
            if src_kw["source_type"] == "lab_lists":
                for lab_name, std_is_numbers in LAB_STANDARD_MAP.items():
                    lab = lab_map.get(lab_name)
                    for is_num in std_is_numbers:
                        std_obj = standard_map.get(is_num)
                        if lab and std_obj:
                            await db.execute(
                                standard_laboratory.insert()
                                .values(standard_id=std_obj.id, laboratory_id=lab.id)
                                .prefix_with("OR IGNORE" if engine.dialect.name == "sqlite" else "")
                            )

            summary["created"] += 1

        # Link labs to standards via association table (for non-lab_list standards)
        # Done with direct inserts — the ORM relationships are lazy="raise".
        for lab_name, std_is_numbers in LAB_STANDARD_MAP.items():
            lab = lab_map.get(lab_name)
            if not lab:
                continue
            for is_num in std_is_numbers:
                std_obj = standard_map.get(is_num)
                if std_obj:
                    await db.execute(
                        standard_laboratory.insert()
                        .values(standard_id=std_obj.id, laboratory_id=lab.id)
                        .prefix_with("OR IGNORE" if engine.dialect.name == "sqlite" else "")
                    )

        # Link IS 12034:2025 related to IS 8921:2024 (example)
        std_8921 = standard_map.get("IS 8921:2024")
        std_12034 = standard_map.get("IS 12034:2025")
        if std_8921 and std_12034:
            await db.execute(
                standard_related.insert()
                .values(base_standard_id=std_8921.id, related_standard_id=std_12034.id)
                .prefix_with("OR IGNORE" if engine.dialect.name == "sqlite" else "")
            )

        await db.commit()
    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Seed BIS sample data")
    parser.add_argument("--clear", action="store_true", help="Wipe tables first")
    args = parser.parse_args()
    print(asyncio.run(seed(clear=args.clear)))
