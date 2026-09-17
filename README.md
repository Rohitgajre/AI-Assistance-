# AskBuddy

AskBuddy is a small Streamlit chat application that sends a question to Google
Gemini via LangChain and keeps the conversation visible for the current browser
session. It is intentionally a focused application, not a database-backed or
authenticated service.

## Features

- Gemini model selection through `ASKBUDDY_MODEL`
- Per-session conversation history and a clear-conversation control
- Reusable model client, bounded retries, and a request timeout
- Friendly provider-error handling without displaying secrets or stack traces
- Unit tests for the provider-independent response boundary

## Architecture

`AI_chatbot.py` is the Streamlit entry point. It loads local configuration,
renders the UI, and owns session state. `chat_service.py` contains the small,
testable model-invocation boundary. Gemini is an external integration; no API,
database, authentication system, document ingestion pipeline, or vector store is
present in this repository.

## Requirements

- Python 3.10 or newer
- A Google Gemini API key

## Local setup

```bash
python -m venv .venv
.venv\Scripts\activate  # PowerShell on Windows
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `GOOGLE_API_KEY` in `.env`. `GEMINI_API_KEY` is also accepted. To select a
different supported model, set `ASKBUDDY_MODEL`; otherwise AskBuddy uses
`gemini-2.5-flash`.

Run the app:

```bash
streamlit run AI_chatbot.py
```

## Development and testing

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
python -m ruff check .
```

Tests do not call Gemini or require an API key. Before deployment, use your
platform's secret manager for the API key, set a resource limit and HTTPS at the
hosting layer, and verify the chosen model is enabled for the deployed key.

## Troubleshooting

- **Missing key:** create `.env` from `.env.example` and set one supported key.
- **Provider error:** verify the key, network access, quota, and model name.
- **Dependencies fail to install:** use a supported Python version in a clean
  virtual environment.

See [PROJECT_AUDIT_REPORT.md](PROJECT_AUDIT_REPORT.md) for the production-readiness audit.

## Local document analysis with Ollama

The optional **Document analysis** panel in the sidebar accepts PDF, DOCX, TXT,
Markdown, CSV, XLSX, PPTX, and image uploads. Files are processed in memory for
the current browser session; their original bytes are never written to disk.

Install the additional parsers, then install and start Ollama separately:

```powershell
python -m pip install -r requirements.txt
ollama pull llama3.1:8b
ollama serve
```

In a second terminal, start AskBuddy as usual. Optional settings in `.env` are
`OLLAMA_BASE_URL`, `OLLAMA_MODEL`, and `OLLAMA_TIMEOUT`; they do not affect the
Gemini chat. OCR additionally requires the Tesseract system executable to be
installed and available on `PATH`.

Upload a document in the sidebar, select **Process upload**, then either
**Analyze document** or ask a grounded question. If Ollama is unavailable, only
that action fails with a clear message; Gemini chat remains available.
