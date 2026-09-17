"""Optional Streamlit controls for the isolated document-analysis feature."""

from __future__ import annotations

import streamlit as st

from document_analysis.config import OllamaSettings
from document_analysis.exceptions import DocumentAnalysisError
from document_analysis.extractors import SUPPORTED_EXTENSIONS
from document_analysis.service import DocumentAnalysisService, OllamaClient


def render_document_analysis() -> None:
    """Render session-scoped upload, analysis, and grounded Q&A controls."""
    settings = OllamaSettings.from_environment()
    service = DocumentAnalysisService(OllamaClient(settings), settings)
    with st.sidebar:
        st.divider()
        st.subheader("Document analysis")
        uploaded = st.file_uploader(
            "Upload one document",
            type=sorted(SUPPORTED_EXTENSIONS),
            max_upload_size=20,
            key="document_upload",
            help="The file is processed in this browser session and is not saved to disk.",
        )
        if uploaded and st.button("Process upload", key="process_document", width="stretch"):
            try:
                st.session_state.document_record = service.ingest(
                    uploaded.name, uploaded.getvalue()
                )
                st.session_state.document_analysis_result = None
                st.success("Document processed. Select Analyze document below.")
            except DocumentAnalysisError as error:
                st.error(str(error))

        document = st.session_state.get("document_record")
        if not document:
            return

        st.caption(f"Loaded: {document.filename} ({document.status.value})")
        if st.button("Analyze document", key="analyze_document", width="stretch"):
            try:
                with st.spinner("Analyzing with local Ollama…"):
                    st.session_state.document_analysis_result = service.analyze(document)
            except DocumentAnalysisError as error:
                st.error(str(error))

        result = st.session_state.get("document_analysis_result")
        if result:
            with st.expander("Latest analysis", expanded=True):
                st.markdown(result.analysis.executive_summary)
                st.caption(f"Model: {result.model} · {result.processing_time_seconds}s")
                st.json(result.analysis.model_dump())

        with st.form("document_question"):
            question = st.text_input("Ask about this document")
            ask = st.form_submit_button("Ask Ollama", width="stretch")
        if ask and question.strip():
            try:
                with st.spinner("Finding an answer…"):
                    answer = service.ask(document, question.strip())
                st.markdown(answer.answer)
                st.caption(
                    "Sources: " + ", ".join(item.section for item in answer.source_references)
                )
            except DocumentAnalysisError as error:
                st.error(str(error))
