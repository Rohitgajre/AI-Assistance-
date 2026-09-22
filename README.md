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

- Accepts bank-statement files from multiple banks and layouts in several formats:
  **PDF** (text-based and image-based / scanned), **CSV**, **Excel (`.xlsx`)**, **TXT**,
  and statement **images (PNG/JPG/JPEG)**.
- **Auto-detects** PDF type (text vs. image) and routes scanned pages to the
  built-in OCR engine when native text is missing.
- Spreadsheet exports (CSV/XLSX) are parsed through the table-aware pipeline,
  including statements that omit a running-balance column.
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

- Upload and process bank statements (PDF, CSV, Excel, TXT, images)
- Auto-detect PDF type (text vs. image)
- Accurate extraction with fallbacks for unusual layouts
- Transaction classification without LLMs
- Export to Excel/CSV
- Running-balance arithmetic warnings (edge-case detection)
- Password-protected PDF handling
- Gemini-powered Q&A over the extracted statement

## How statement processing works (no LLM involved)

Every step from upload to Excel/CSV export is deterministic and LLM-free.
Gemini and Ollama are **optional** layers that answer questions about the
extracted data afterwards — they never extract, parse, or classify anything.

```
upload (PDF / CSV / XLSX / TXT / image)
        │
        ▼
1. route by file type ──────────────────────────────────────────────┐
        │                                                           │
   PDF ─────────────► 2a. native text? ──no──► rasterize (pymupdf) ─┤
   CSV/XLSX ───────► rows (csv / openpyxl)                          │
   PNG/JPG/JPEG ───► RapidOCR                                       │
   TXT ────────────► decode text                                    │
        ▼                                                           ▼
3. transaction extraction (3 strategies, best wins)          OCR text
   ├─ line parser: header row + date-buffered chunks
   ├─ date-window parser: split on date regexes
   └─ table parser: column-header mapping (incl. no-balance)
        │  dedupe │
        ▼
4. normalize dates (→ YYYY-MM-DD) and amounts (₹, commas, CR/DR, () )
        ▼
5. classify without an LLM
   ├─ heuristic keyword rules (18 categories, 0.72+ confidence wins)
   └─ multinomial Naive Bayes (trained on an in-code corpus)
        ▼
6. validate running-balance arithmetic → warnings (never blocks)
        ▼
7. export CSV (stdlib) or XLSX (openpyxl, Summary + Transactions)
```

### Step 1 — Route by file type

`process_bank_file` in `AI_chatbot.py` inspects the extension and magic bytes
(`%PDF`) and dispatches to the right entry point on `BankStatementProcessor`:

- `process_pdf(bytes)` — text PDFs and scanned PDFs
- `process_tabular(rows)` — CSV and Excel (`csv` / `openpyxl`)
- `process_image(bytes)` — statement screenshots/photos (RapidOCR)
- `process_text(text)` — plain-text statements

### Step 2 — Get text out of the file (OCR only when needed)

Text-backed PDFs are read in order of reliability:

1. **pymupdf** — native text extraction, rows rebuilt from word coordinates so
   table alignment survives `get_text("words")`.
2. **pypdf** — fallback when pymupdf output is not "useful".
3. **Built-in OCR** — only when no native text exists (scanned PDFs): each page is
   rasterized with pymupdf at 2× zoom and passed to the hardcoded **RapidOCR**
   engine (ONNX runtime, models shipped with the wheel). Recognized boxes are
   regrouped into lines by vertical position and sorted left-to-right so table
   reading order is preserved. Tesseract is an *optional* fallback only.

`_text_looks_useful` gates all of this: extracted text must contain at least 40
characters **and** a date or money token — otherwise the next strategy (or OCR)
is tried.

PDFs that also contain real tables are additionally parsed with
`page.find_tables()`; CSV/XLSX arrive as rows directly.

### Step 3 — Extract transactions (three strategies, best wins)

All strategies are pure heuristics operating on regexes and column position:

- **Line parser** — finds the header row (a line containing `DATE` plus narration
  / debit / credit / balance tokens), then buffers lines between date tokens and
  parses each buffer as one transaction (supports wrapped narration).
- **Date-window parser** — splits the text on `_DATE_TOKEN` matches and parses
  every chunk independently; catches layouts the line parser misses.
- **Table parser** — detects the header mapping per column
  (`date`, `narration/particulars/description`, `withdrawal/debit`,
  `deposit/credit`, `balance`, `amount`) and extracts each row by column index.
  It also handles **statements with no balance column** and multi-line narration.

Rows from the three strategies are deduplicated on
(date, description, debit, credit, balance) and the strategy returning the most
rows wins — exactly how the PDF pipeline and the CSV/XLSX pipeline behave.

### Step 4 — Normalize dates and amounts

- Dates are converted to ISO `YYYY-MM-DD` across formats such as `dd/mm/yyyy`,
  `dd-mm-yy`, `dd-mmm-yyyy`, `yyyy-mm-dd`, `dd.mm.yyyy`, and `dd Mon yyyy`.
- Amounts strip `₹`, `INR`/`Rs`, thousands separators, `CR`/`DR` flags, and
  parentheses — `(1,000.00)` becomes a debit of `1000.00`.
- When a row has two amounts (debit + credit, no balance) the amount with a
  `CR`/`DR` flag or a matching description keyword decides the side.

### Step 5 — Classify transactions without an LLM

`_classify_description` is a two-tier, fully local classifier:

1. **Heuristic keyword rules** — curated keywords per category (Salary,
   Entertainment, Housing, Transport, Bills, Cash Withdrawal, Groceries, Food,
   Shopping, Healthcare, Insurance, Investment, Transfer, Interest, Bank
   Charges, Income, Other Expense, Unknown). A match earns ~0.93 confidence
   (0.70 for Transfer), and if that confidence is **≥ 0.72** it wins outright.
2. **Multinomial Naive Bayes** — a lightweight in-code model trained on a compact
   corpus per category. It wins only when heuristics are not confident and the
   ML probability is **≥ 0.45**.

Unmatched credits become `Income`, unmatched debits become `Other Expense`.
Neither step calls Gemini or Ollama, so processing works with no API key and
no network.

### Step 6 — Validate (deterministic, non-blocking)

After classification the pipeline checks running-balance arithmetic:
`expected = previous balance − debit + credit`. Mismatches larger than
`₹1.00` are reported as warnings ("OCR or layout gaps are likely") — the
extraction is never rejected.

### Step 7 — Export

- **CSV** via the standard-library `csv` module.
- **Excel (`.xlsx`)** via `openpyxl` with a **Summary** sheet (account details,
  totals, transaction count) plus a **Transactions** sheet.

Export columns: `date`, `description`, `debit_amount`, `credit_amount`,
`balance`, `category`, `confidence`. Everything above runs in memory — nothing
is written to disk except the file the user downloads.

> **LLMs are not part of this pipeline.** The Gemini chat and the optional
> Ollama document-analysis panel operate *on top of* the extracted data; turning
> their API keys or servers off does not affect statement processing.

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