"""DOCX extraction."""

from io import BytesIO

from document_analysis.exceptions import InvalidDocumentError
from document_analysis.schemas import DocumentChunk, SourceReference


def extract_docx(content: bytes) -> list[DocumentChunk]:
    try:
        from docx import Document

        text = "\n".join(
            paragraph.text for paragraph in Document(BytesIO(content)).paragraphs
        ).strip()
        if not text:
            raise InvalidDocumentError("No readable text was found in the DOCX file.")
        return [
            DocumentChunk(text=text, source=SourceReference(section="document", excerpt=text[:300]))
        ]
    except ImportError as error:
        raise InvalidDocumentError("DOCX support is unavailable; install python-docx.") from error
    except InvalidDocumentError:
        raise
    except Exception as error:
        raise InvalidDocumentError("The DOCX file could not be read.") from error
