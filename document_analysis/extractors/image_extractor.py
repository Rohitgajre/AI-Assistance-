"""OCR extraction, available only when the optional OCR stack is configured."""

from io import BytesIO

from document_analysis.exceptions import InvalidDocumentError
from document_analysis.schemas import DocumentChunk, SourceReference


def extract_image(content: bytes) -> list[DocumentChunk]:
    try:
        import pytesseract
        from PIL import Image

        text = pytesseract.image_to_string(Image.open(BytesIO(content))).strip()
        if not text:
            raise InvalidDocumentError("OCR found no readable text in the image.")
        return [DocumentChunk(text=text, source=SourceReference(section="OCR", excerpt=text[:300]))]
    except ImportError as error:
        raise InvalidDocumentError(
            "OCR support is unavailable; install pytesseract and Tesseract OCR."
        ) from error
    except InvalidDocumentError:
        raise
    except Exception as error:
        raise InvalidDocumentError("The image could not be processed by OCR.") from error
