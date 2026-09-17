"""Feature-specific exceptions; callers can keep the rest of the app available."""


class DocumentAnalysisError(Exception):
    """Base exception for document analysis failures."""


class InvalidDocumentError(DocumentAnalysisError):
    """The upload is unsupported, too large, unreadable, or empty."""


class OllamaUnavailableError(DocumentAnalysisError):
    """Ollama is not running or did not respond in time."""


class InvalidModelResponseError(DocumentAnalysisError):
    """The local model did not return the required validated response shape."""
