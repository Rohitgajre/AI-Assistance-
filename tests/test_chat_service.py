"""Tests for provider-independent chat behavior."""

from types import SimpleNamespace

import pytest

import AI_chatbot
from chat_service import ask_model


class FakeModel:
    def __init__(self, content: object) -> None:
        self.content = content
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> SimpleNamespace:
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.content)


def test_ask_model_returns_trimmed_provider_content() -> None:
    model = FakeModel("  Hello!  ")

    assert ask_model(model, "Hi") == "Hello!"
    assert model.prompts == ["Hi"]


def test_ask_model_extracts_text_from_structured_content() -> None:
    content = [
        {"type": "text", "text": "**AI Automation** combines AI and automation."},
        {"type": "text", "text": "It can adapt to new situations."},
    ]

    assert ask_model(FakeModel(content), "What is AI automation?") == (
        "**AI Automation** combines AI and automation.\n"
        "It can adapt to new situations."
    )


def test_ask_model_rejects_empty_provider_content() -> None:
    with pytest.raises(ValueError, match="empty response"):
        ask_model(FakeModel("  "), "Hi")


def test_main_does_not_expose_document_analysis_hook() -> None:
    assert not hasattr(AI_chatbot, "render_document_analysis")
