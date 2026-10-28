# Phase A — Repository Audit and Architecture Plan

**Date:** 2026-10-27  
**Status:** Complete  
**Tests before changes:** 53 passed, 0 failed

## 1) Repository inspection (findings)

### Entry points & structure
- `AI_chatbot.py` — Main Streamlit app (Gemini chat + bank statement processing UI). Uses `langchain-google-genai`, `chat_service.ask_model`. Has document upload handling for both statements and general docs (routes to `document_analysis`).
- `app.py` — ASGI shim only (deployment compatibility), not the real UI.
- `bank_statement_processor.py` — Core bank statement pipeline (extraction, classification, export). **LLM-free, deterministic.** Heavily tested.
- `chat_service.py` — Thin Gemini provider wrapper.
- `document_analysis/` — Isolated Ollama-based document analysis (ingest/chunk/retrieve/analyze/ask). Uses its own OCR extractors.
- `tests/` — 53 passing tests covering bank statements, chat service, document analysis, statement pipeline.

### Dependencies (requirements.txt)
- `streamlit>=1.37,<2`, `langchain-google-genai>=2,<4`, `python-dotenv>=1,<2`
- `pypdf>=5,<7`, `pymupdf>=1.24,<2`, `python-docx>=1,<2`, `openpyxl>=3.1,<4`, `python-pptx>=1,<2`
- `pytesseract>=0.3.10,<1` (optional), `Pillow>=10,<12`, `rapidocr>=3,<4`, `onnxruntime>=1.17,<2`
- `pytest>=8,<9`, `ruff>=0.8,<1`
- Python >=3.10 (pyproject says)

### Existing integrations
- **Gemini**: `AI_chatbot.py` creates `ChatGoogleGenerativeAI` via `langchain_google_genai`, keys from `GOOGLE_API_KEY`/`GEMINI_API_KEY`. Configurable model via `ASKBUDDY_MODEL` (default `gemini-3.6-flash`). `chat_service.ask_model` extracts text from responses.
- **Ollama**: `document_analysis/` with `OllamaClient`, `OllamaSettings` from env (`OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT`). Isolated from Gemini.
- **OCR**: `document_analysis/extractors/ocr_engine.py` hardcodes RapidOCR (ONNX), pytesseract optional fallback. Used by bank processor and document analysis.
- **Parsers**: pymupdf/pypdf for PDFs, python-docx, openpyxl, python-pptx, Pillow.

## 2) Feature classification

| Feature | Status | Notes |
|---------|--------|-------|
| Bank statement PDF/CSV/XLSX/TXT/image extraction | **Implemented** | Robust, well-tested, LLM-free |
| Transaction classification (heuristic + NB) | **Implemented** | Non-LLM as required |
| CSV/XLSX export | **Implemented** | Summary + Transactions sheets |
| Gemini chat over statement context | **Implemented** | Works |
| Password-protected PDFs | **Implemented** | Handled |
| OCR (RapidOCR) for scanned docs/images | **Implemented** | Shared engine |
| Document analysis (Ollama) sidebar | **Implemented** | Simple, in-memory only |
| Docling integration | **Missing** | Need to add |
| Generic multi-document ingestion/persistence | **Missing** | Only single doc in session state |
| Persistent vector store (embeddings/RAG) | **Missing** | document_analysis uses keyword retrieval only |
| Structure-aware chunking with provenance | **Missing** | Basic fixed-size chunking only |
| Agent orchestration/tool calling | **Missing** | None |
| Tavily web search | **Missing** | None |
| Persistent chat history (SQLite) | **Missing** | Only Streamlit session state |
| Document registry/deletion/reindexing | **Missing** | None |
| Structured analysis for arbitrary spreadsheets | **Missing** | Only bank statement specific |
| Hybrid retrieval/reranking | **Missing** | None |
| Content hashing/caching | **Missing** | None |

## 3) Risks identified

1. **Breaking bank statement functionality** - CRITICAL. Must not change `bank_statement_processor.py` behavior in breaking ways. All 53 tests must remain green.
2. **Docling dependency conflicts** - Docling can pull in heavy deps (transformers, torch in some installs). May conflict with rapidocr/onnxruntime versions. Need to check compatibility carefully.
3. **Overwriting existing document_analysis** - Currently integrated in UI. Need to be careful about migration.
4. **API changes** - Must preserve public interfaces where practical.
5. **Performance** - Don't slow down existing flows.

## 4) Proposed architecture (incremental, non-breaking)

### New modules to add (alongside existing)
```
ingestion/           # Generic document ingestion
  docling_converter.py
  document_router.py
  normalization.py
  validators.py

rag/                 # RAG pipeline
  chunking.py
  embeddings.py
  vector_store.py    # Chroma-based, behind interface
  retrieval.py
  answer_generation.py

agent/               # Controlled orchestration
  orchestrator.py
  router.py
  tool_schemas.py
  tools/
    document_search.py
    structured_analysis.py
    tavily_search.py
    conversation_context.py

memory/              # Memory & persistence
  conversation_store.py  # SQLite
  document_registry.py
  preferences.py

providers/           # Provider abstractions
  llm_provider.py
  embedding_provider.py

config/              # Centralized config (or extend existing)
  settings.py

utils/               # Shared utilities
  hashing.py
  logging.py
```

### Integration strategy
- Keep `bank_statement_processor.py` untouched in behavior (only fix bugs if absolutely necessary - we already fixed one test issue)
- Keep `document_analysis/` working; can be extended or gradually migrated
- Add new services that can be used by enhanced UI
- Feature-flag new capabilities where sensible
- All new code behind interfaces for testability

## 5) Implementation plan (phased)

### Phase A (DONE) — Audit and plan ✓
- Inspected repo, read key files, ran tests (53 passed)
- Documented findings, risks, architecture

### Phase B — Docling-based generic ingestion
- Add docling to requirements (check compatibility first)
- Create `ingestion/validators.py` (file type/MIME/signature, size limits)
- Create `ingestion/normalization.py` (common document representation)
- Create `ingestion/docling_converter.py` (Docling integration, OCR handling)
- Create `ingestion/document_router.py` (route to appropriate processor)
- Keep bank statement processor as specialized path
- Add tests for ingestion

### Phase C — Structure-aware chunking, embeddings, vector store, RAG
- Add vector store deps (chromadb)
- Create `rag/chunking.py` (structure-aware, preserve tables/heads)
- Create `providers/embedding_provider.py` (configurable)
- Create `rag/embeddings.py`
- Create `rag/vector_store.py` (interface + Chroma impl)
- Create `rag/retrieval.py`
- Create `rag/answer_generation.py`
- Integration with existing LLM providers

### Phase D — Memory, document registry, safe deletion
- Create `memory/conversation_store.py` (SQLite)
- Create `memory/document_registry.py`
- Create `memory/preferences.py`
- Session/user isolation

### Phase E — Tavily + controlled tool routing/orchestration
- Add tavily-python
- Create `agent/tool_schemas.py`
- Create `agent/tools/*`
- Create `agent/router.py`
- Create `agent/orchestrator.py`
- Integrate with providers

### Phase F — Structured analysis + bank statement integration
- Create `app/services/structured_analysis.py` or similar for spreadsheets
- Ensure bank statement pipeline remains unchanged and integrated
- Safe code execution constraints (no arbitrary LLM code execution)

### Phase G — Streamlit improvements, security, evaluation, docs
- Enhance UI for multi-document, citations, sources separation
- Security hardening
- Add evaluation tests
- Update README, .env.example
- Run all tests, lint

## 6) Immediate next steps
1. Start Phase B - check docling installation/compatibility in current env
2. Add minimal deps carefully
3. Build ingestion layer without breaking existing code
4. Test incrementally
