"""Plain-text extraction."""

from document_analysis.exceptions import InvalidDocumentError


def extract_text(content: bytes) -> str:
    """Decode a text upload and reject empty input."""
    text = content.decode("utf-8-sig", errors="replace").strip()
    if not text:
        raise InvalidDocumentError("The uploaded document is empty.")
    return text
