"""Unified ingestion service combining Docling and specialized processors."""
from __future__ import annotations

import logging
import uuid

from ingestion.docling_converter import DoclingConverter
from ingestion.document_router import route_document
from ingestion.normalization import NormalizedDocument
from ingestion.validators import validate_upload

logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(self):
        self.docling = DoclingConverter(enable_ocr=True)

    def warm(self) -> None:
        """Load the Docling models ahead of time.

        Docling's first use pulls the layout/table models into memory, which
        takes ~90s on CPU. Paying that during the first upload looks like a
        freeze, so the app warms it in a background thread at startup.
        """
        self.docling.warm()
    
    def ingest(self, filename: str, content: bytes, use_cache: bool = True) -> tuple[NormalizedDocument | None, str]:
        """
        Ingest a document. Returns (normalized_doc, strategy_used)
        For bank statements, caller should handle separately with specialized processor.
        """
        validation = validate_upload(filename, content)
        if not validation.valid:
            raise ValueError(validation.error)
        
        route = route_document(filename, content)
        document_id = str(uuid.uuid4())
        
        if route.use_specialized and route.strategy == "bank_statement":
            # Let caller handle with bank_statement_processor
            return None, "bank_statement_specialized"
        
        if route.use_docling:
            try:
                normalized = self.docling.convert(filename, content, document_id)
                return normalized, route.strategy
            except Exception as e:
                logger.warning(f"Docling failed, trying fallbacks: {e}")
                # Could try other methods here
                raise
        
        raise ValueError(f"Cannot process file type: {filename}")


# Global instance
_ingestion_service = None


def get_ingestion_service() -> IngestionService:
    global _ingestion_service
    if _ingestion_service is None:
        _ingestion_service = IngestionService()
    return _ingestion_service
