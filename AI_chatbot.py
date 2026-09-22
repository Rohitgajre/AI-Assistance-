"""Streamlit entry point for AskBuddy — Gemini-style chat plus bank-statement processing."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from typing import Any, Final

import streamlit as st
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

from bank_statement_processor import BankStatement, BankStatementProcessor, Transaction
from chat_service import ask_model
from document_analysis.config import OllamaSettings
from document_analysis.exceptions import DocumentAnalysisError
from document_analysis.service import DocumentAnalysisService, OllamaClient

load_dotenv()

LOGGER = logging.getLogger(__name__)
DEFAULT_MODEL: Final = "gemini-3.6-flash"
MODEL_ENV_VAR: Final = "ASKBUDDY_MODEL"
API_KEY_ENV_VARS: Final = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
CHAT_FILE_TYPES: Final = ["pdf", "application/pdf", "txt", "docx", "csv", "xlsx", "png", "jpg", "jpeg"]
STATEMENT_EXTENSIONS: Final = frozenset({"pdf", "txt", "csv", "xlsx", "png", "jpg", "jpeg", "webp"})
SPREADSHEET_EXTENSIONS: Final = frozenset({"csv", "xlsx"})
IMAGE_EXTENSIONS: Final = frozenset({"png", "jpg", "jpeg", "webp"})

SUGGESTIONS = {
    "Process a bank statement": "Process the attached bank statement and classify every transaction.",
    "Summarize spending": "Summarize spending by category from the processed statement.",
    "Export a recap": "Give me a short recap I can keep with the Excel export.",
    "Ask about a document": "What are the key findings in the uploaded file?",
}


def inject_shell_styles() -> None:
    """Gemini/ChatGPT canvas: centered hero, quiet sidebar, floating composer."""
    st.html(
        """
        <style>
        [data-testid="stHeader"], header, .stAppHeader {
            background: transparent !important;
            border: none !important;
        }
        .block-container {
            max-width: 48rem;
            padding-top: 4rem;
            padding-bottom: 7rem;
        }
        [data-testid="stSidebar"] {
            min-width: 17rem;
            max-width: 17rem;
        }
        [data-testid="stSidebar"] button {
            justify-content: flex-start;
        }
        [data-testid="stChatMessage"] {
            background: transparent;
            border: none;
        }
        [data-testid="stChatInput"] {
            padding-bottom: 1.2rem;
        }
        [data-testid="stChatInput"] textarea {
            font-size: 1rem;
        }
        .hero-wrap {
            text-align: center;
            padding: 4.5rem 0 1.5rem;
        }
        .hero-kicker {
            color: #9aa0a6;
            font-size: 0.95rem;
            margin-bottom: 0.6rem;
        }
        .hero-title {
            font-size: clamp(2.4rem, 5vw, 3.4rem);
            font-weight: 500;
            letter-spacing: -0.04em;
            line-height: 1.15;
            background: linear-gradient(90deg, #8ab4f8 0%, #c58af9 48%, #f28b82 100%);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
            margin: 0;
        }
        .hero-sub {
            color: #c4c7c5;
            margin-top: 0.85rem;
            font-size: 1.05rem;
        }
        </style>
        """
    )


@st.cache_resource(show_spinner=False)
def get_processor() -> BankStatementProcessor:
    return BankStatementProcessor()


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


def initialise_session_state() -> None:
    """Initialize session state for chat, documents, and statements."""
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("document_record", None)
    st.session_state.setdefault("document_name", None)
    st.session_state.setdefault("document_loaded", False)
    st.session_state.setdefault("bank_statement", None)
    st.session_state.setdefault("pdf_password", "")
    st.session_state.setdefault("pending_prompt", "")


def clear_chat_state() -> None:
    """Clear chat, document, and statement context."""
    st.session_state.messages = []
    st.session_state.document_record = None
    st.session_state.document_name = None
    st.session_state.document_loaded = False
    st.session_state.bank_statement = None
    st.session_state.pending_prompt = ""
    st.session_state.pop("_last_turn", None)


def get_document_context(document_record: object) -> str:
    """Build a compact document-context string for prompt injection."""
    if document_record is None:
        return ""
    chunks = getattr(document_record, "chunks", [])
    text_parts = [chunk.text for chunk in chunks if getattr(chunk, "text", "").strip()]
    return "\n\n".join(text_parts)[:12000]


def get_statement_context(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    summary = payload.get("summary", {})
    rows = payload.get("transactions", [])[:80]
    lines = [
        f"Bank: {summary.get('bank_name')}",
        f"Holder: {summary.get('account_holder_name')}",
        f"Account: {summary.get('account_number')}",
        f"IFSC: {summary.get('ifsc_code')}",
        f"PDF type: {summary.get('document_type')}",
        f"Transactions: {summary.get('transaction_count')}",
    ]
    for row in rows:
        lines.append(
            f"{row.get('date')} | {row.get('description')} | "
            f"Dr {row.get('debit_amount')} Cr {row.get('credit_amount')} | "
            f"{row.get('category')}"
        )
    return "\n".join(lines)[:12000]


def stream_text(text: str) -> Iterator[str]:
    """Yield a text stream in small chunks to simulate flowing model output."""
    if not text:
        return
    pieces = re.findall(r".{1,180}\S*|.{1,180}", text)
    yield from pieces


def handle_uploaded_document(uploaded_file) -> None:
    """Persist extracted document metadata and content for later Q&A."""
    if uploaded_file is None:
        return
    settings = OllamaSettings.from_environment()
    service = DocumentAnalysisService(OllamaClient(settings), settings)
    try:
        record = service.ingest(uploaded_file.name, uploaded_file.getvalue())
    except DocumentAnalysisError as error:
        st.session_state.document_loaded = False
        st.session_state.document_record = None
        st.session_state.document_name = None
        st.session_state.document_error = str(error)
        return
    st.session_state.document_record = record
    st.session_state.document_name = uploaded_file.name
    st.session_state.document_loaded = True


def statement_payload(statement: BankStatement) -> dict[str, Any]:
    return {
        "summary": statement.as_summary(),
        "warnings": statement.warnings,
        "transactions": [item.as_record() for item in statement.transactions],
    }


def payload_to_transactions(payload: dict[str, Any]) -> list[Transaction]:
    return [Transaction(**row) for row in payload.get("transactions", [])]


def file_extension(name: str) -> str:
    """Return the lower-cased extension of a filename (``""`` when absent)."""
    return name.lower().rsplit(".", 1)[-1] if "." in name else ""


def read_statement_rows(name: str, data: bytes) -> list[list[object]]:
    """Read CSV/XLSX cells into rows for tabular statement processing."""
    extension = file_extension(name)
    if extension == "csv":
        import csv
        from io import StringIO

        return [list(row) for row in csv.reader(StringIO(data.decode("utf-8-sig", errors="replace")))]
    from io import BytesIO

    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    rows: list[list[object]] = []
    for sheet in workbook.worksheets:
        rows.extend(list(row) for row in sheet.iter_rows(values_only=True))
    return rows


def process_bank_file(uploaded_file, password: str = "") -> BankStatement:
    """Route any supported statement upload (PDF/CSV/XLSX/TXT/image) to the processor."""
    processor = get_processor()
    name = uploaded_file.name
    data = uploaded_file.getvalue()
    extension = file_extension(name)
    if extension == "pdf" or b"%PDF" in data[:1024]:
        return processor.process_pdf(data, name, password=password)
    if extension in SPREADSHEET_EXTENSIONS:
        return processor.process_tabular(read_statement_rows(name, data), name)
    if extension in IMAGE_EXTENSIONS:
        return processor.process_image(data, name)
    return processor.process_text(data.decode("utf-8", errors="ignore"), name)


def render_sidebar() -> None:
    """Quiet ChatGPT-style rail: new chat and optional settings."""
    with st.sidebar:
        st.markdown("**AskBuddy**")
        st.caption("Gemini chat · statement OCR")
        if st.button("New chat", icon=":material/edit_square:", width="stretch", key="new_chat"):
            clear_chat_state()
            st.rerun()

        if st.session_state.messages:
            st.caption("This chat")
            preview = next(
                (msg["content"] for msg in st.session_state.messages if msg.get("role") == "user" and msg.get("content")),
                "Current conversation",
            )
            st.button(preview[:42], width="stretch", disabled=True, key="recent_chat")
        else:
            st.caption("No chats yet")

        with st.popover("Settings", icon=":material/tune:"):
            st.text_input(
                "PDF password",
                type="password",
                key="pdf_password",
                help="Only needed for password-protected bank PDFs.",
            )

        if st.session_state.get("bank_statement"):
            summary = st.session_state.bank_statement["summary"]
            st.caption(summary.get("file_name") or "Statement ready")
            st.badge("Statement", icon=":material/picture_as_pdf:", color="blue")


def render_empty_state() -> None:
    """Centered Gemini greeting with suggestion cards."""
    st.html(
        """
        <div class="hero-wrap">
            <div class="hero-kicker">AskBuddy</div>
            <p class="hero-title">Hello, how can I help you today?</p>
            <p class="hero-sub">Attach a bank statement (PDF, CSV, Excel, TXT, or image), or ask anything about your documents.</p>
        </div>
        """
    )
    labels = list(SUGGESTIONS.keys())
    top = st.columns(2, gap="small")
    bottom = st.columns(2, gap="small")
    for index, label in enumerate(labels):
        column = top[index] if index < 2 else bottom[index - 2]
        with column:
            if st.button(label, width="stretch", key=f"suggest_{index}"):
                st.session_state.pending_prompt = SUGGESTIONS[label]
                st.rerun()


def render_statement_card(payload: dict[str, Any], key_prefix: str) -> None:
    summary = payload.get("summary", {})
    rows = payload.get("transactions", [])
    cols = st.columns(4)
    cols[0].metric("Transactions", summary.get("transaction_count") or 0)
    cols[1].metric("Debits", f"{float(summary.get('total_debit') or 0):,.2f}")
    cols[2].metric("Credits", f"{float(summary.get('total_credit') or 0):,.2f}")
    cols[3].metric("PDF type", str(summary.get("document_type") or "text").title())

    st.markdown(
        f"**{summary.get('account_holder_name') or 'Account holder unknown'}**  \n"
        f"Account `{summary.get('account_number') or '—'}` · IFSC `{summary.get('ifsc_code') or '—'}`  \n"
        f"{summary.get('bank_name') or 'Bank not detected'}"
    )
    for warning in payload.get("warnings") or []:
        st.caption(warning)

    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info("The PDF was read, but no transaction rows matched this layout.")

    processor = get_processor()
    transactions = payload_to_transactions(payload)
    statement = BankStatement(
        file_name=str(summary.get("file_name") or "statement.pdf"),
        document_type=str(summary.get("document_type") or "text"),
        bank_name=summary.get("bank_name"),
        account_holder_name=summary.get("account_holder_name"),
        account_number=summary.get("account_number"),
        ifsc_code=summary.get("ifsc_code"),
        transactions=transactions,
        warnings=list(payload.get("warnings") or []),
    )
    export_cols = st.columns(2)
    with export_cols[0]:
        st.download_button(
            "Download CSV",
            data=processor.export_bytes(transactions, "csv", statement),
            file_name="bank_statement.csv",
            mime="text/csv",
            icon=":material/download:",
            width="stretch",
            key=f"{key_prefix}_csv",
        )
    with export_cols[1]:
        st.download_button(
            "Download Excel",
            data=processor.export_bytes(transactions, "xlsx", statement),
            file_name="bank_statement.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:",
            width="stretch",
            key=f"{key_prefix}_xlsx",
        )


def render_messages() -> None:
    """Render chat history, including embedded statement results."""
    for index, message in enumerate(st.session_state.messages):
        avatar = ":material/person:" if message["role"] == "user" else ":material/auto_awesome:"
        with st.chat_message(message["role"], avatar=avatar):
            if message.get("content"):
                st.markdown(message["content"])
            payload = message.get("statement")
            if payload:
                render_statement_card(payload, key_prefix=f"msg_{index}")


def build_prompt(question: str) -> str:
    """Create a grounded prompt that uses statement or document context when present."""
    base = (
        "You are AskBuddy. Answer helpfully. When bank-statement context is present, "
        "use those extracted rows. Do not reclassify transactions; categories are already assigned "
        "by a non-LLM engine."
    )
    statement_context = get_statement_context(st.session_state.get("bank_statement"))
    if statement_context:
        return f"{base}\n\nBANK STATEMENT CONTEXT:\n{statement_context}\n\nUSER QUESTION:\n{question}"
    if st.session_state.document_record is not None:
        context = get_document_context(st.session_state.document_record)
        if context:
            return f"{base}\n\nDOCUMENT CONTEXT:\n{context}\n\nUSER QUESTION:\n{question}"
    return f"{base}\n\nUSER QUESTION:\n{question}"


def generate_response(prompt: str) -> str:
    """Generate the final answer text from the configured Gemini model."""
    model_name = os.getenv(MODEL_ENV_VAR, DEFAULT_MODEL)
    llm = create_llm(model_name, configured_api_key())
    return ask_model(llm, prompt)


def consume_prompt(prompt: Any) -> tuple[str, list[Any]]:
    if prompt is None:
        return "", []
    if isinstance(prompt, str):
        return prompt.strip(), []
    text = (getattr(prompt, "text", None) or "").strip()
    files = list(getattr(prompt, "files", None) or [])
    return text, files


def is_statement_file(uploaded_file) -> bool:
    """True when the upload looks like a bank statement rather than a general document."""
    name = uploaded_file.name
    data = uploaded_file.getvalue()
    return file_extension(name) in STATEMENT_EXTENSIONS or b"%PDF" in data[:1024]


def handle_turn(user_text: str, files: list[Any]) -> None:
    display = user_text or ("Uploaded " + ", ".join(item.name for item in files))
    st.session_state.messages.append({"role": "user", "content": display})
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(display)
        for item in files:
            st.caption(item.name)

    statement_payload_data = None
    status_notes: list[str] = []
    for item in files:
        if is_statement_file(item):
            try:
                statement = process_bank_file(
                    item,
                    password=str(st.session_state.get("pdf_password") or ""),
                )
                statement_payload_data = statement_payload(statement)
                st.session_state.bank_statement = statement_payload_data
                status_notes.append(
                    f"Processed **{item.name}** as a {statement.document_type} statement "
                    f"with {len(statement.transactions)} classified transactions."
                )
            except Exception as error:
                status_notes.append(str(error))
        elif statement_payload_data is None:
            handle_uploaded_document(item)
            if st.session_state.get("document_error"):
                status_notes.append(st.session_state.document_error)
                st.session_state.document_error = ""

    question = user_text or "Process this file."
    with st.chat_message("assistant", avatar=":material/auto_awesome:"):
        with st.status(":shimmer[Thinking]", type="compact") as status:
            if statement_payload_data:
                with st.status("Reading statement and classifying transactions", type="step"):
                    st.caption("Heuristic rules plus multinomial Naive Bayes. Scanned pages use built-in OCR.")
                status.update(label="Done", state="complete")
            else:
                status.update(label="Done", state="complete")

        intro = "\n\n".join(status_notes)
        if statement_payload_data:
            if intro:
                st.markdown(intro)
            render_statement_card(statement_payload_data, key_prefix="live")
            assistant_text = intro or "Statement processed."
        elif not files and "bank statement" in question.lower() and not st.session_state.get("bank_statement"):
            assistant_text = "Attach a bank statement (PDF, CSV, Excel, TXT, or image) with the + button, then send. Password-protected files can be unlocked in Settings."
            st.markdown(assistant_text)
        elif configured_api_key():
            answer = generate_response(build_prompt(question))
            st.write_stream(stream_text(answer))
            assistant_text = answer
        else:
            assistant_text = (
                "Attach a bank statement (PDF, CSV, Excel, TXT, or image) with the + button "
                "to extract and classify transactions. "
                "Set GOOGLE_API_KEY to chat as well."
            )
            st.markdown(assistant_text)

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": assistant_text if not statement_payload_data else intro,
            "statement": statement_payload_data,
        }
    )


def main() -> None:
    """Render the AskBuddy workspace and orchestrate chat plus statement flows."""
    st.set_page_config(
        page_title="AskBuddy",
        page_icon=":material/auto_awesome:",
        layout="centered",
        initial_sidebar_state="expanded",
    )
    inject_shell_styles()
    initialise_session_state()
    render_sidebar()

    if not st.session_state.messages:
        render_empty_state()
    else:
        render_messages()

    prompt = st.chat_input(
        "Ask anything, or attach a bank statement (PDF, CSV, Excel, TXT, or image)",
        accept_file="multiple",
        file_type=CHAT_FILE_TYPES,
        max_upload_size=50,
        submit_mode="disable",
    )

    pending = st.session_state.get("pending_prompt") or ""
    if pending and prompt is None:
        st.session_state.pending_prompt = ""
        handle_turn(pending, [])
        st.rerun()

    user_text, files = consume_prompt(prompt)
    if user_text or files:
        signature = (user_text, tuple(sorted(item.name for item in files)))
        if st.session_state.get("_last_turn") != signature:
            st.session_state._last_turn = signature
            handle_turn(user_text, files)
            st.rerun()


if __name__ == "__main__":
    main()
