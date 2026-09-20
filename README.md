# AskBuddy — Bank Statement Processing & Classification System

A fully functional prototype that processes bank statement PDFs (both text-based
and image-based / scanned), extracts account and transaction data, classifies
transactions with **non-LLM** approaches, and exports the results to **Excel**
and **CSV**.

AskBuddy is packaged as a small Streamlit application with three parts:

- **Bank-statement pipeline** — extraction, classification, and export
  (`bank_statement_processor.py`)
- **Gemini chat** — ask natural-language questions about the processed statement
- **Document analysis (optional)** — grounded Q&A over other document types using
  a local Ollama model

## Features

### Input handling (§3.1)

- Accepts bank-statement PDFs from multiple banks and layouts.
- Supports **text-based** and **image-based (scanned)** PDFs.
- **Auto-detects** PDF type (text vs. image) and routes scanned pages to the
  built-in OCR engine when native text is missing.
- Other upload types are accepted in chat: TXT, DOCX, CSV, XLSX, PNG, JPG.

### Data extraction (§3.2)

- Account holder details: bank name, account holder name, account number, IFSC.
- Transaction table data: **date, description, debit amount, credit amount,
  balance**.
- Multiple extraction strategies per layout (line parsing, date-window parsing,
  embedded PDF table extraction) with deduplication, plus running-balance
  validation warnings.

### OCR (hardcoded, generic)

- The document OCR engine is **hardcoded to RapidOCR** (ONNX runtime, models
  shipped with the wheel). No system executable such as Tesseract needs to be
  installed.
- The same engine powers image uploads, scanned-PDF fallback in the bank
  pipeline, and the document-analysis extractors.
- A Tesseract binary on `PATH` is used only as an *optional* fallback.

### Classification engine — non-LLM (§3.3)

Transactions are classified **without LLMs** using a hybrid approach:

1. **Heuristic rule-based matching** — curated keyword rules per category
   (Salary, Entertainment, Housing, Transport, Bills, Cash Withdrawal,
   Groceries, Food, Shopping, Healthcare, Insurance, Investment, Transfer,
   Interest, Bank Charges, Income, Other Expense, Unknown).
2. **Traditional machine learning** — a multinomial **Naive Bayes** model trained
   on a compact in-code corpus; used when heuristics are not confident.

The winning category and confidence are assigned per transaction. The method used
(heuristic vs. machine learning) is tracked internally and never shown in the
export output.

### Output requirements (§4)

Export processed data in:

- **Excel (`.xlsx`)** — a Summary sheet plus a Transactions sheet
- **CSV (`.csv`)**

Export columns: `date`, `description`, `debit_amount`, `credit_amount`,
`balance`, `category`, `confidence`.

### Key features (§5)

- Upload and process bank statements
- Auto-detect PDF type (text vs. image)
- Accurate extraction with fallbacks for unusual layouts
- Transaction classification without LLMs
- Export to Excel/CSV
- Running-balance arithmetic warnings (edge-case detection)
- Password-protected PDF handling
- Gemini-powered Q&A over the extracted statement

## Architecture

```
AI_chatbot.py                Streamlit entry point: UI, session state, routing
bank_statement_processor.py  Extraction + classification + export pipeline
document_analysis/           Optional isolated document analysis (Ollama)
chat_service.py              Small Gemini provider boundary
```

`bank_statement_processor.py` is the assessment core: it parses PDFs with pypdf
(+ pymupdf rasterization for OCR), classifies with heuristics and a multinomial
Naive Bayes model, and exports with the standard library CSV module or openpyxl.

## Requirements

- Python 3.10 or newer
- A Google Gemini API key (`GOOGLE_API_KEY`, or `GEMINI_API_KEY`)
- Optional: Ollama for the document-analysis panel

## Local setup

```powershell
python -m venv .venv
.venv\Scripts\activate  # PowerShell on Windows
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `GOOGLE_API_KEY` in `.env`. To select a different Gemini model, set
`ASKBUDDY_MODEL` (default: `gemini-3.6-flash`).

Run the app:

```powershell
streamlit run AI_chatbot.py
```

Drop a bank-statement PDF into the chat and ask to process it, or use the
`bank_statement_processor` functions directly from Python:

```python
from bank_statement_processor import BankStatementProcessor

processor = BankStatementProcessor()
statement = processor.process_pdf(pdf_bytes, "statement.pdf")
processor.export_transactions(statement, "output/statement.xlsx")
```

## Optional document analysis (Ollama)

The sidebar **Document analysis** panel accepts PDF, DOCX, TXT, Markdown, CSV,
XLSX, PPTX, and image uploads, processes them in memory, and answers questions
with a local Ollama model.

```powershell
ollama pull llama3.1:8b
ollama serve
```

Optional `.env` settings: `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT`.
These do not affect the Gemini chat.

## Development and testing

```powershell
python -m pip install -r requirements.txt
python -m pytest
python -m ruff check .
```

Tests do not call Gemini or Ollama and do not require API keys. OCR engine tests
run against mocked backends; a real end-to-end OCR check needs RapidOCR models,
which are downloaded on first use.