"""Centralized embedding provider abstraction."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class EmbeddingConfig:
    provider: str = "sentence-transformers"  # sentence-transformers, gemini, ollama
    model: str = "all-MiniLM-L6-v2"
    api_key: str | None = None
    base_url: str | None = None


class EmbeddingProvider:
    def __init__(self, config: EmbeddingConfig | None = None):
        self.config = config or self._load_config()
        self._embeddings = None
    
    def _load_config(self) -> EmbeddingConfig:
        return EmbeddingConfig(
            provider=os.getenv("EMBEDDING_PROVIDER", "sentence-transformers"),
            model=os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
            api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"),
        )
    
    def get_embeddings(self):
        if self._embeddings is not None:
            return self._embeddings
        
        if self.config.provider == "sentence-transformers":
            try:
                from langchain_community.embeddings import HuggingFaceEmbeddings
                self._embeddings = HuggingFaceEmbeddings(
                    model_name=self.config.model,
                    model_kwargs={'device': 'cpu'},
                    encode_kwargs={'normalize_embeddings': True}
                )
                return self._embeddings
            except Exception as e:
                raise ValueError(f"Failed to initialize sentence-transformers: {e}") from e
        
        elif self.config.provider == "gemini":
            try:
                from langchain_google_genai import GoogleGenerativeAIEmbeddings
                if not self.config.api_key:
                    raise ValueError("Google API key not configured for embeddings")
                self._embeddings = GoogleGenerativeAIEmbeddings(
                    model=self.config.model or "models/embedding-001",
                    google_api_key=self.config.api_key,
                )
                return self._embeddings
            except Exception as e:
                raise ValueError(f"Failed to initialize Gemini embeddings: {e}") from e
        
        elif self.config.provider == "ollama":
            try:
                from langchain_community.embeddings import OllamaEmbeddings
                self._embeddings = OllamaEmbeddings(
                    model=self.config.model or "nomic-embed-text",
                    base_url=self.config.base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                )
                return self._embeddings
            except Exception as e:
                raise ValueError(f"Failed to initialize Ollama embeddings: {e}") from e
        
        raise ValueError(f"Unknown embedding provider: {self.config.provider}")


_embedding_provider = None


def get_embedding_provider() -> EmbeddingProvider:
    global _embedding_provider
    if _embedding_provider is None:
        _embedding_provider = EmbeddingProvider()
    return _embedding_provider
