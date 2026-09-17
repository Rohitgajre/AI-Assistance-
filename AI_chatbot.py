"""Streamlit entry point for the AskBuddy Gemini chat application."""

from __future__ import annotations

import logging
import os
from typing import Final

import streamlit as st
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

from chat_service import ask_model

load_dotenv()

LOGGER = logging.getLogger(__name__)
DEFAULT_MODEL: Final = "gemini-3.6-flash"
MODEL_ENV_VAR: Final = "ASKBUDDY_MODEL"
API_KEY_ENV_VARS: Final = ("GOOGLE_API_KEY", "GEMINI_API_KEY")


def configured_api_key() -> str | None:
    """Return the configured Google API key without logging its value."""
    return next((os.getenv(name) for name in API_KEY_ENV_VARS if os.getenv(name)), None)


@st.cache_resource(show_spinner=False)
def create_llm(model_name: str, api_key: str) -> ChatGoogleGenerativeAI:
    """Create one reusable chat model instance for the Streamlit process."""
    return ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=api_key,
        temperature=0.2,
        max_retries=2,
        timeout=30,
    )


def initialise_chat_history() -> None:
    """Create the per-session transcript only when it does not already exist."""
    if "messages" not in st.session_state:
        st.session_state.messages = []


def main() -> None:
    """Render the application and process one user message per rerun."""
    st.set_page_config(page_title="AskBuddy", page_icon="🤖")
    st.title("🤖 AskBuddy – AI Q&A Bot")
    st.caption("A conversational assistant powered by LangChain and Google Gemini.")

    api_key = configured_api_key()
    if not api_key:
        st.error("Set GOOGLE_API_KEY or GEMINI_API_KEY in .env before starting AskBuddy.")
        return

    initialise_chat_history()
    if st.sidebar.button("Clear conversation", width="stretch"):
        st.session_state.messages = []
        st.rerun()

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Ask a question")
    if not prompt:
        return

    prompt = prompt.strip()
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    model_name = os.getenv(MODEL_ENV_VAR, DEFAULT_MODEL)
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                answer = ask_model(create_llm(model_name, api_key), prompt)
            except Exception:
                LOGGER.exception("Gemini request failed")
                st.error(
                    "I couldn't get a response. Check your API key, connection, "
                    "and quota, then try again."
                )
                return
        st.markdown(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
