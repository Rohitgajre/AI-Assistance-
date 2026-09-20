"""OCR extraction for image uploads, using the built-in RapidOCR engine.

The engine is hardcoded (self-contained, no system binary required) and the
pipeline stays generic: any decodable image is handled through the shared
``ocr_engine`` module also used for scanned PDFs and bank statements.
"""

from io import BytesIO

from document_analysis.exceptions import InvalidDocumentError
from document_analysis.extractors.ocr_engine import OCRUnavailableError, ocr_image
from document_analysis.schemas import DocumentChunk, SourceReference


def extract_image(content: bytes) -> list[DocumentChunk]:
    try:
        from PIL import Image

        text = ocr_image(Image.open(BytesIO(content))).strip()
        if not text:
            raise InvalidDocumentError("OCR found no readable text in the image.")
        return [DocumentChunk(text=text, source=SourceReference(section="OCR", excerpt=text[:300]))]
    except OCRUnavailableError as error:
        raise InvalidDocumentError(str(error)) from error
    except InvalidDocumentError:
        raise
    except Exception as error:
        raise InvalidDocumentError("The image could not be processed by OCR.") from error