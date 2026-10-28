"""File validation for uploads."""
from __future__ import annotations

import os
from dataclasses import dataclass

# Safe defaults
DEFAULT_MAX_FILE_SIZE_BYTES = int(os.getenv("ASKBUDDY_MAX_UPLOAD_MB", "50")) * 1024 * 1024
DEFAULT_MAX_PAGES = int(os.getenv("ASKBUDDY_MAX_PAGES", "200"))
DEFAULT_MAX_PROCESSING_TIME_SEC = int(os.getenv("ASKBUDDY_MAX_PROCESSING_TIME_SEC", "300"))


SUPPORTED_EXTENSIONS = {
    "pdf", "docx", "pptx", "xlsx", "csv", "txt", "md", "markdown",
    "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp", "heic", "heif"
}

MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "txt": "text/plain",
    "md": "text/markdown",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


@dataclass
class ValidationResult:
    valid: bool
    error: str | None = None
    extension: str = ""
    mime_type: str | None = None


def get_extension(filename: str) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def validate_upload(filename: str, content: bytes, max_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES) -> ValidationResult:
    if not filename:
        return ValidationResult(valid=False, error="Missing filename")
    if not content:
        return ValidationResult(valid=False, error="Empty file")
    ext = get_extension(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        return ValidationResult(valid=False, error=f"Unsupported file type: .{ext}", extension=ext)
    if len(content) > max_size_bytes:
        return ValidationResult(valid=False, error=f"File exceeds size limit ({max_size_bytes // (1024*1024)} MB)", extension=ext)
    # Basic PDF signature check
    if ext == "pdf" and not content.startswith(b"%PDF"):
        # Could be corrupted, but let converter handle - just warn
        pass
    return ValidationResult(valid=True, extension=ext)
