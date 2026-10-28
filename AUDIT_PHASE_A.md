# Phase A: Repository Audit & Architecture Plan

## Repository Structure (as-is)
```
AI-Assistance-/
├── AI_chatbot.py           # Streamlit entry point (Gemini chat + bank statement UI)
├── app.py                  # (exists - need to look at it)
├── bank_statement_processor.py # Core bank statement pipeline (extraction, classification, export) - LLM-free
├── chat_service.py         # Gemini provider helper
├── requirements.txt
├── pyproject.toml
├── .env.example
├── document_analysis/      # Optional Ollama-based document analysis (isolated)
│   ├── config.py
│   ├── exceptions.py
│   ├── schemas.py
│   ├── service.py
│   ├── ui.py
│   ├── extractors/
│   │   ├── __init__.py     # extract_document router
│   │   ├── base.py
│   │   ├── pdf.py
│   │   ├── docx.py
│   │   ├── pptx.py
│   │   ├── xlsx.py
│   │   ├── txt.py
│   │   ├── image.py
│   │   └── ocr_engine.py   # RapidOCR (hardcoded) + pytesseract fallback
│   └── prompts/
└── tests/                  # 53 passing tests (1 was fixed)
```

## Feature Classification

### Implemented
- **Bank statement pipeline (complete)**: PDF (text/scanned), CSV, XLSX, TXT, images. OCR via RapidOCR. Heuristic + NaiveBayes classification (non-LLM). Exports CSV/XLSX with Summary+Transactions. Validation, reconciliation, warnings. All deterministic - LLM not involved.
- **Gemini chat integration**: `langchain-google-genai`, `chat_service.py`, API key from env. Model configurable via `ASKBUDDY_MODEL`.
- **Document analysis (Ollama)**: Isolated panel in UI, supports PDF/DOCX/TXT/MD/CSV/XLSX/PPTX/images. In-memory chunking/retrieval. Analysis + Q&A. Uses its own Ollama client. Settings in `document_analysis/config.py`.
- **OCR engine**: `document_analysis/extractors/ocr_engine.py` - RapidOCR hardcoded, pytesseract optional fallback. Shared by bank statements and document analysis.
- **Streamlit UI**: Gemini-style chat UI in `AI_chatbot.py` with statement processing flow.

### Partially Implemented
- **Document analysis** is present but very simple - basic chunking, keyword-based retrieval (`_retrieve` does term frequency), no vector store, no embeddings, no persistence, no citations beyond source refs, no web search, no agent orchestration. It's "document Q&A" but not a proper RAG pipeline as specified.
- **Multi-document support**: Not really - document_analysis stores single document record in session state. No document registry, no multi-doc comparison infrastructure.
- **Memory/conversation history**: Only Streamlit session state (`st.session_state.messages`) - not durable/persistent across sessions. No SQLite/persistent storage.
- **Tool calling/orchestration**: None. Just direct calls.
- **Web search (Tavily)**: Not implemented.
- **Docling integration**: Not present.

### Missing
- **Docling-based generic ingestion** as primary converter
- **Persistent vector store** (Chroma or similar) with proper embeddings
- **Structure-aware chunking** with metadata (page numbers, sections, chunk IDs)
- **Hybrid retrieval, reranking** (optional but infrastructure missing)
- **Controlled agent orchestration with tool schemas** (document_search, structured_data_query, tavily_search, conversation_context)
- **Tavily web search integration**
- **Persistent chat history (SQLite)** separate from vector indexes
- **Document registry with indexing status, deletion, reindexing**
- **Structured data analysis for spreadsheets** beyond bank statements (safe, no arbitrary code execution)
- **Proper grounding, citations (filename + page/section)**, hallucination safeguards
- **Content hashing, caching of conversions/embeddings**
- **Session/user isolation, authorization checks**

### Broken/Issues
- All tests pass currently (53 passed after fixing the one test). Good baseline.
- `app.py` exists - need to check it.
- The requirement says "Keep the existing bank statement processor as a specialized financial-processing component. Generic document ingestion must not break its transaction extraction, classification, validation, or CSV/XLSX exports." - must be very careful not to break this.

## Dependencies Analysis
Current: streamlit, langchain-google-genai, python-dotenv, pypdf, pymupdf, python-docx, openpyxl, python-pptx, pytesseract (optional), Pillow, rapidocr, onnxruntime, pytest, ruff. Python >=3.10.

Missing for requirements: docling, chromadb (or similar vector store), langchain/langgraph? (requirements say "Do not add LangGraph solely for the sake of using a framework" - so only if beneficial), tavily-python, sqlalchemy/aiosqlite or just sqlite3, sentence-transformers or provider embeddings, etc. Need to be careful about compatibility.

## Risks
1. **Breaking bank statement pipeline** - highest risk. Must preserve all existing behavior. Tests must keep passing.
2. **Docling dependencies can be heavy** - need to check compatibility with existing packages (especially rapidocr/onnxruntime versions). Docling has its own deps.
3. **Adding many new dependencies** may conflict.
4. **Streamlit app structure** - need to integrate new features without breaking existing UI flow.
5. **The existing document_analysis is used by AI_chatbot.py** - need to see if we need to replace or augment it.

## Target Architecture (proposed, incremental)
Keep existing files working. Add new modules alongside:

```
app/ (or add to root with clear separation)
├── ingestion/
│   ├── docling_converter.py   # Docling integration with fallbacks
│   ├── document_router.py     # route by type, decide strategy
│   ├── normalization.py       # normalize to common representation
│   └── validators.py
├── rag/
│   ├── chunking.py            # structure-aware chunking
│   ├── embeddings.py          # configurable embedding provider
│   ├── vector_store.py        # Chroma interface + impl
│   ├── retrieval.py           # semantic/hybrid retrieval
│   └── answer_generation.py   # grounded generation
├── agent/
│   ├── orchestrator.py        # controlled orchestration
│   ├── router.py              # question routing
│   ├── tool_schemas.py        # typed schemas
│   └── tools/
│       ├── document_search.py
│       ├── structured_analysis.py
│       ├── tavily_search.py
│       └── conversation_context.py
├── memory/
│   ├── conversation_store.py  # SQLite persistent history
│   ├── document_registry.py   # document metadata/indexing status
│   └── preferences.py
├── providers/
│   ├── llm_provider.py        # centralized LLM config
│   └── embedding_provider.py  # centralized embedding config
└── services/
    └── bank_statement_service.py # wrapper around existing processor if needed
```

But the instruction says "Adapt to the repository instead of imposing a structure blindly. This is an illustrative structure, not a requirement to move every existing file. Preserve established paths and public interfaces when practical."

So better to add modules in a way that integrates with existing codebase. Maybe create `app/` package or just add modules at root level? Let us look up how others interpret - but better to be flexible.

Also need to understand `app.py` - is it used?
