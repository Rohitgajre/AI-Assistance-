"""Isolated environment-based configuration for document analysis."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OllamaSettings:
    """Settings intentionally independent from the Gemini configuration."""

    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    timeout_seconds: int = 120
    chunk_size: int = 6_000
    chunk_overlap: int = 400
    max_upload_bytes: int = 20 * 1024 * 1024

    @classmethod
    def from_environment(cls) -> OllamaSettings:
        timeout = _positive_int("OLLAMA_TIMEOUT", cls.timeout_seconds)
        return cls(
            base_url=os.getenv("OLLAMA_BASE_URL", cls.base_url).rstrip("/"),
            model=os.getenv("OLLAMA_MODEL", cls.model),
            timeout_seconds=timeout,
        )


def _positive_int(name: str, default: int) -> int:
    """Read an optional positive integer without breaking app startup."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
