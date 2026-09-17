"""Document text extraction dispatch."""

from __future__ import annotations

from document_analysis.exceptions import InvalidDocumentError
from document_analysis.schemas import DocumentChunk, SourceReference

from .docx_extractor import extract_docx
from .pdf_extractor import extract_pdf
from .spreadsheet_extractor import extract_spreadsheet
from .text_extractor import extract_text

TEXT_EXTENSIONS = {"txt", "md", "markdown"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {
    "pdf",
    "docx",
    "csv",
    "xlsx",
    "pptx",
    "png",
    "jpg",
    "jpeg",
}


def extract_document(filename: str, content: bytes) -> list[DocumentChunk]:
    """Extract supported upload bytes without persisting the original file."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in SUPPORTED_EXTENSIONS:
        raise InvalidDocumentError(f"Unsupported file type: .{extension or 'none'}")
    if extension in TEXT_EXTENSIONS:
        return [
            DocumentChunk(
                text=extract_text(content), source=SourceReference(section="document", excerpt="")
            )
        ]
    if extension == "pdf":
        return extract_pdf(content)
    if extension == "docx":
        return extract_docx(content)
    if extension in {"csv", "xlsx"}:
        return extract_spreadsheet(content, extension)
    if extension == "pptx":
        from .pptx_extractor import extract_pptx

        return extract_pptx(content)
    from .image_extractor import extract_image

    return extract_image(content)
