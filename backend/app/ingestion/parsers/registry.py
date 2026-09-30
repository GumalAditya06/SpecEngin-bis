"""Parser registry: one place that maps source types to parser classes.

The ingestion pipeline dispatches through this registry (see
``app.ingestion.pipeline.Pipeline._parse``). Source-type names follow
CLAUDE.md; the historical short aliases stay registered so older callers
don't break.
"""

from typing import Dict, Type, Optional


class ParserRegistry:
    """Registry of available parsers, keyed by source type."""

    def __init__(self):
        self._parsers: Dict[str, Type] = {}

    def register(self, source_type: str, parser_class: Type) -> None:
        self._parsers[source_type] = parser_class

    def get(self, source_type: str) -> Optional[Type]:
        return self._parsers.get(source_type)

    def all(self) -> Dict[str, Type]:
        return self._parsers.copy()


# Global registry instance — populated below at import time.
registry = ParserRegistry()


def _register_defaults() -> None:
    from app.ingestion.parsers.pdf_parser import IndigenousPdfParser
    from app.ingestion.parsers.qco_parser import QcoParser
    from app.ingestion.parsers.kys_parser import KysParser
    from app.ingestion.parsers.scheme_reg_parser import SchemeRegPdfParser
    from app.ingestion.parsers.hallmarking_parser import HallmarkingParser
    from app.ingestion.parsers.lab_list_parser import LabListParser

    registry.register("indigenous_pdf", IndigenousPdfParser)
    registry.register("qco", QcoParser)
    registry.register("kys_catalogue", KysParser)
    registry.register("scheme_reg", SchemeRegPdfParser)
    # Canonical name per CLAUDE.md + legacy alias used by older call sites.
    registry.register("hallmarking_docs", HallmarkingParser)
    registry.register("hallmarking", HallmarkingParser)
    registry.register("lab_lists", LabListParser)
    registry.register("lab_list", LabListParser)


_register_defaults()


def get_parser(source_type: str) -> Optional[Type]:
    """Return the parser class registered for this source type, if any."""
    return registry.get(source_type)
