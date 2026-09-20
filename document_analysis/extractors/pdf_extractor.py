"""PDF extraction with page-level references and a built-in OCR fallback.

Text-backed PDFs are parsed with pypdf. Image-only (scanned) PDFs are detected
when no native text is found and are routed through the shared hardcoded OCR
engine, so the same generic pipeline serves every document type.
"""

from io import BytesIO

from document_analysis.exceptions import InvalidDocumentError
from document_analysis.extractors.ocr_engine import OCRUnavailableError, ocr_pdf
from document_analysis.schemas import DocumentChunk, SourceReference


def extract_pdf(content: bytes) -> list[DocumentChunk]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        chunks = []
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                chunks.append(
                    DocumentChunk(
                        text=text,
                        source=SourceReference(section=f"page {number}", excerpt=text[:300]),
                    )
                )
        if chunks:
            return chunks
        return _extract_scanned_pdf(content)
    except OCRUnavailableError as error:
        raise InvalidDocumentError(str(error)) from error
    except InvalidDocumentError:
        raise
    except ImportError as error:
        raise InvalidDocumentError("PDF support is unavailable; install pypdf.") from error
    except Exception as error:
        raise InvalidDocumentError("The PDF could not be read.") from error


def _extract_scanned_pdf(content: bytes) -> list[DocumentChunk]:
    """OCR every page of an image-only PDF and label the chunks accordingly."""
    try:
        ocr_pages = ocr_pdf(content)
    except ValueError as error:
        raise InvalidDocumentError(str(error)) from error
    if not ocr_pages:
        raise InvalidDocumentError("No readable text was found in the PDF.")
    chunks = []
    for number, text in enumerate(ocr_pages, start=1):
        chunks.append(
            DocumentChunk(
                text=text,
                source=SourceReference(section=f"page {number} (OCR)", excerpt=text[:300]),
            )
        )
    return chunks