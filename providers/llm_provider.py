"""Centralized LLM provider abstraction."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol


class ChatModelProtocol(Protocol):
    def invoke(self, prompt: str) -> Any:
        ...


@dataclass
class ProviderConfig:
    provider: str = "gemini"  # gemini, ollama
    model: str = ""
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.2
    max_tokens: int | None = None
    timeout: int = 30


class LLMProvider:
    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or self._load_config()
        self._model = None
    
    def _load_config(self) -> ProviderConfig:
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        return ProviderConfig(
            provider="gemini",
            model=os.getenv("ASKBUDDY_MODEL", "gemini-3.6-flash"),
            api_key=api_key,
        )
    
    def get_chat_model(self) -> ChatModelProtocol:
        if self._model is not None:
            return self._model
        
        if self.config.provider == "gemini":
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                if not self.config.api_key:
                    raise ValueError("Google API key not configured")
                self._model = ChatGoogleGenerativeAI(
                    model=self.config.model,
                    google_api_key=self.config.api_key,
                    temperature=self.config.temperature,
                    max_retries=2,
                    timeout=self.config.timeout,
                )
                return self._model
            except Exception as e:
                raise ValueError(f"Failed to initialize Gemini: {e}") from e
        
        elif self.config.provider == "ollama":
            try:
                from langchain_community.chat_models import ChatOllama
                self._model = ChatOllama(
                    model=self.config.model or os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
                    base_url=self.config.base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                    temperature=self.config.temperature,
                )
                return self._model
            except Exception as e:
                raise ValueError(f"Failed to initialize Ollama: {e}") from e
        
        raise ValueError(f"Unknown provider: {self.config.provider}")
    
    def is_available(self) -> bool:
        try:
            self.get_chat_model()
            return True
        except Exception:
            return False


_llm_provider = None


def get_llm_provider() -> LLMProvider:
    global _llm_provider
    if _llm_provider is None:
        _llm_provider = LLMProvider()
    return _llm_provider
