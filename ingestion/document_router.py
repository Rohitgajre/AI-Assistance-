"""Route documents to appropriate processing strategy."""
from __future__ import annotations

from ingestion.validators import get_extension

BANK_STATEMENT_HINTS = {
    # Strong, unambiguous signals that a file is a statement export. Weak words
    # such as "credit", "debit" or "balance" appear in ordinary documents (a
    # "Credit Policy.pdf"), and a false positive here means the file is sent to
    # the statement parser - and possibly through OCR - before being retried as
    # a normal document.
    "statement", "bank", "passbook", "ledger", "transaction", "ifsc"
}

SPREADSHEET_EXTS = {"csv", "xlsx"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp", "heic", "heif"}
PDF_EXTS = {"pdf"}
OFFICE_EXTS = {"docx", "pptx"}


class DocumentRoute:
    def __init__(self, strategy: str, use_docling: bool = True, use_specialized: bool = False, reason: str = ""):
        self.strategy = strategy  # bank_statement, spreadsheet_analysis, docling_generic, ocr_image, etc.
        self.use_docling = use_docling
        self.use_specialized = use_specialized
        self.reason = reason


def route_document(filename: str, content: bytes) -> DocumentRoute:
    ext = get_extension(filename)
    name_lower = filename.lower()
    
    # Bank statements get specialized treatment
    if ext in PDF_EXTS or ext in {"txt"} or ext in IMAGE_EXTS or ext in SPREADSHEET_EXTS:
        # Check if it looks like a bank statement
        name_hint = any(h in name_lower for h in BANK_STATEMENT_HINTS)
        # Also could be detected by content, but filename is quick check
        if name_hint or ext in SPREADSHEET_EXTS and any(h in name_lower for h in {"bank", "statement"}):
            return DocumentRoute(
                strategy="bank_statement",
                use_docling=False,
                use_specialized=True,
                reason="Bank statement detected - using specialized processor"
            )
        # For PDFs that might be statements, be cautious - but let caller decide
        # Better to be permissive and let the flow handle it
    
    # Images - could use OCR
    if ext in IMAGE_EXTS:
        return DocumentRoute(
            strategy="image_ocr",
            use_docling=True,  # Docling can handle images
            use_specialized=False,
            reason="Image file"
        )
    
    # Spreadsheets - Docling can handle, but also direct parsing better for calculations
    if ext in SPREADSHEET_EXTS:
        return DocumentRoute(
            strategy="spreadsheet",
            use_docling=True,
            use_specialized=False,
            reason="Spreadsheet - can use Docling or direct parsers"
        )
    
    # Office docs and general PDFs
    if ext in OFFICE_EXTS or ext in PDF_EXTS:
        return DocumentRoute(
            strategy="document",
            use_docling=True,
            use_specialized=False,
            reason="Document suitable for Docling conversion"
        )
    
    if ext in {"txt", "md", "markdown"}:
        return DocumentRoute(
            strategy="text",
            use_docling=True,
            use_specialized=False,
            reason="Text document"
        )
    
    return DocumentRoute(
        strategy="unknown",
        use_docling=False,
        use_specialized=False,
        reason="Unknown type"
    )
