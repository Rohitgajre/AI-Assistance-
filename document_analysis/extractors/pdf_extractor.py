"""PDF extraction with page-level references."""

from io import BytesIO

from document_analysis.exceptions import InvalidDocumentError
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
        if not chunks:
            raise InvalidDocumentError("No readable text was found in the PDF.")
        return chunks
    except ImportError as error:
        raise InvalidDocumentError("PDF support is unavailable; install pypdf.") from error
    except InvalidDocumentError:
        raise
    except Exception as error:
        raise InvalidDocumentError("The PDF could not be read.") from error
