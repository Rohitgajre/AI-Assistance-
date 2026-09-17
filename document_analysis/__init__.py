"""Optional, local-only document analysis powered by Ollama."""

from .config import OllamaSettings
from .service import DocumentAnalysisService, OllamaClient

__all__ = ["DocumentAnalysisService", "OllamaClient", "OllamaSettings"]
