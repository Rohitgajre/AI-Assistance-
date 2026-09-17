"""Small, framework-independent helpers for model interaction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class ChatModel(Protocol):
    """The subset of a LangChain chat model used by this application."""

    def invoke(self, prompt: str) -> Any:
        """Return a model response for ``prompt``."""


def ask_model(model: ChatModel, prompt: str) -> str:
    """Invoke a chat model and return a non-empty text response."""
    response = model.invoke(prompt)
    content = getattr(response, "content", response)
    answer = _content_to_text(content)
    if not answer:
        raise ValueError("The model returned an empty response")
    return answer


def _content_to_text(content: Any) -> str:
    """Extract text from either plain or provider-structured message content."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, Mapping):
        return _content_to_text(content.get("text", ""))
    if isinstance(content, list):
        text_blocks = [
            _content_to_text(block)
            for block in content
            if isinstance(block, (str, Mapping))
        ]
        return "\n".join(block for block in text_blocks if block).strip()
    return str(content).strip()
