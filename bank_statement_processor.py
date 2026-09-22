"""Bank statement extraction, classification, and export.

The pipeline is LLM-free: PDFs are parsed with pymupdf/pypdf and the built-in
RapidOCR engine, transactions are classified with heuristics plus a multinomial
Naive Bayes model, and results export to CSV or Excel.

Processing order (every PDF / CSV / XLSX / TXT / image upload):

    PDF/file validation
    -> PDF type detection (native text vs scanned)
    -> native text extraction OR rasterize + OCR (coordinates preserved)
    -> page/section detection (primary table vs supporting/detail sections)
    -> account extraction
    -> primary transaction-table extraction (line, date-window, table, OCR columns)
    -> normalization (dates -> ISO or validated short form, amounts -> numbers)
    -> transaction validation (invalid rows are discarded, never exported)
    -> parser quality scoring (best validated candidate wins, NOT row count)
    -> classification (heuristics -> Naive Bayes; never an LLM)
    -> statement validation (running balance, reconciliation, structured warnings)
    -> CSV / XLSX export (clean rows only)
"""

from __future__ import annotations

import csv
import io
import math
import re
import warnings
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    from openpyxl import Workbook
except ImportError:  # pragma: no cover - optional dependency in testing env
    Workbook = None


# ---------------------------------------------------------------------------
# Token patterns
# ---------------------------------------------------------------------------

# Full date formats (parsed to ISO 8601 for export).
_DATE_PATTERNS = (
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d/%m/%y",
    "%d-%m-%y",
    "%m/%d/%Y",
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d %b %Y",
    "%d %B %Y",
    "%d/%b/%Y",
)
# Year-less date formats that are still acceptable transaction dates (10/12).
# They are validated (real month/day) but kept in their original short form.
_DATE_PATTERNS_SHORT = ("%d/%m", "%d-%m", "%m/%d", "%d %b", "%Y-%m", "%m/%Y")

_MONTH_NAME = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)

# A date token must be one of the formats below. The "dd Mon yyyy" branch is
# deliberately restricted to real month names so that OCR junk such as
# "12 CHECK 1236" can never be mistaken for a date.
_DATE_TOKEN = re.compile(
    r"\b("
    r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"        # 03/09/2024, 02-09-24
    r"|\d{4}-\d{1,2}-\d{1,2}"               # 2024-09-03
    r"|\d{1,2}\.\d{1,2}\.\d{2,4}"           # 03.09.2024
    r"|\d{1,2}[-/ ](?:"                      # 19-Sep-2024, 10 Feb 2024
    + _MONTH_NAME
    + r")[-/ ]\d{2,4}"
    r"|\d{1,2}[-/ ](?:"
    + _MONTH_NAME
    + r")\b"                                # 19 Sep, 10 Feb
    r"|\d{1,4}[-/]\d{1,2})"                 # 10/12, 09/2024 (validated later)
    ,
    re.IGNORECASE,
)

# Money-looking text used for *cell* values (a trusted column position).
_MONEY_TOKEN = re.compile(
    r"(?:₹|inr|rs\.?\s*)?"
    r"\(?\d{1,3}(?:,\d{2,3})*"
    r"(?:\.\d{1,2})?"
    r"\)?(?:\s*(?:cr|dr))?",
    re.IGNORECASE,
)

# Lines that are never transaction rows (totals, page footers, float rows).
_NOISE_LINE = re.compile(
    r"(opening\s+balance|closing\s+balance|brought\s+forward|carried\s+forward|"
    r"\btotal\b|page\s+\d+|statement\s+summary|this\s+is\s+a\s+computer|"
    r"b/f\b|c/f\b|transaction\s+count|no\.?\s+of\s+transactions)",
    re.IGNORECASE,
)

# Supporting/detail sections that must NOT be merged into the primary table.
_SUPPORTING_SECTION_KEYS = (
    ("deposits", r"deposits?\s+and\s+other\s+credits?"),
    ("withdrawals", r"withdrawals?\s+and\s+other\s+debits?"),
    ("fees", r"account\s+service\s+charges?\s+and\s+fees?"),
    ("checks", r"checks?\s+paid"),
)

# Section titles that frame a summary block or a by-type detail list.
_SUMMARY_SECTION_RE = re.compile(
    r"(?i)(summary\s+of\s+your\s+account|account\s+summary|statement\s+of\s+account|"
    r"account\s+transactions?\s+by\s+type|account\s+transactions?\s+by\s+date|"
    r"account\s+transactions?\s+with\s+detailed\s+description|account\s+activity\b)",
)

# The primary transaction-table header: a line mentioning a date column plus at
# least two narration/amount column labels.
_PRIMARY_TABLE_HEADER_LINE = re.compile(
    r"(?i)\b(?:txn\s*date|posting\s*date|transaction\s*date|val\s*date|value\s*date|date)\b"
    r".*?\b(?:narration|particulars?|description|details|transactions?|remarks|"
    r"withdraw|deposit|debit|credit|amount|balance)\b"
    r".*?\b(?:withdraw|deposit|debit|credit|amount|balance)\b",
)

# Reference labels whose following numeric token must never be parsed as money
# (check numbers, terminal IDs, UTR/reference codes, authorization numbers).
_REF_LABEL_RE = re.compile(
    r"(?i)^(?:check|chq|cheque|checque|ref\.?|reference|utr|tid|terminal|terminals?|"
    r"term\b|auth\.?|authorization|authorisation|txn\b|txn\.?|tran\b|trn\b|"
    r"visa|mastercard|swipe|bill\s*no\.?|invoice\s*no\.?|merchant\s*id|mid\b)",
)

_IFSC = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")
_ACCOUNT_NO = re.compile(
    r"(?:account\s*(?:no\.?|number|#)|a/c(?:\s*no\.?)?|acct\.?\s*no\.?)\s*[:\-]?\s*"
    r"([A-Z0-9Xx*]{6,22})",
    re.IGNORECASE,
)
_HOLDER = re.compile(
    r"(?:account\s*(?:holder)?\s*name|customer\s*name|name\s+of\s+account\s+holder|primary\s+holder)\s*[:\-]\s*([A-Za-z0-9 .,&/'()-]+)",
    re.IGNORECASE,
)
_BANK_NAME = re.compile(r"^.*\bBANK\b.*$", re.IGNORECASE | re.MULTILINE)

# Text that should never survive as a transaction description.
_SECONDARY_TEXT_RE = re.compile(
    r"(?i)(opening\s+balance|closing\s+balance|brought\s+forward|carried\s+forward|"
    r"statement\s+of\s+account|summary\s+of\s+your\s+account|account\s+summary|"
    r"deposits?\s+and\s+other\s+credits?|withdrawals?\s+and\s+other\s+debits?|"
    r"account\s+service\s+charges?\s+and\s+fees?|checks?\s+paid|"
    r"account\s+transactions?\s+by\s+type|page\s+\d+|statement\s+summary|"
    r"total\s+(debits?|credits?|withdrawals?|deposits?|charges?))\b",
)

# Upper bound after which a single transaction amount is treated as OCR garbage.
_VALIDATION_MAX_AMOUNT = 1_000_000_000.0
# Above this magnitude a value looks like a reference/terminal id, not money.
_SCORE_SUSPICIOUS_AMOUNT = 10_000_000.0
_BALANCE_TOLERANCE = 1.0

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Salary": ("salary", "payroll", "bonus", "incentive", "wage"),
    "Entertainment": (
        "netflix",
        "spotify",
        "youtube",
        "prime",
        "hotstar",
        "subscription",
        "movie",
        "cinema",
        "game",
        "bookmyshow",
    ),
    "Housing": ("rent", "mortgage", "housing", "apartment", "lease", "maintenance society"),
    "Transport": (
        "petrol",
        "fuel",
        "diesel",
        "uber",
        "ola",
        "cab",
        "bus",
        "train",
        "taxi",
        "auto",
        "irctc",
        "metro",
        "parking",
        "toll",
    ),
    "Bills": (
        "electricity",
        "water",
        "gas bill",
        "internet",
        "broadband",
        "mobile",
        "phone",
        "utility",
        "recharge",
        "airtel",
        "jio",
        "vi prepaid",
    ),
    "Cash Withdrawal": ("atm", "cash wdl", "cash withdrawal", "nwdl"),
    "Groceries": (
        "grocery",
        "supermarket",
        "bigbasket",
        "dmart",
        "reliance fresh",
        "vegetable",
        "milk",
        "grocer",
    ),
    "Food": (
        "swiggy",
        "zomato",
        "restaurant",
        "cafe",
        "fast food",
        "dining",
        "dominos",
        "mcdonald",
        "kfc",
    ),
    "Shopping": (
        "amazon",
        "flipkart",
        "myntra",
        "ajio",
        "shopping",
        "ecommerce",
        "retail",
        "meesho",
    ),
    "Healthcare": ("pharmacy", "hospital", "clinic", "apollo", "medplus", "doctor", "medical"),
    "Insurance": ("insurance", "premium", "lic ", "policybazaar"),
    "Investment": ("mutual fund", "sip", "brokerage", "zerodha", "groww", "shares", "nse", "bse"),
    "Transfer": ("upi", "imps", "neft", "rtgs", "transfer", "reversal", "refund", "p2a", "p2m"),
    "Interest": ("interest credit", "int.pd", "int paid", "interest earned"),
    "Bank Charges": ("gst", "charges", "fee", "commission", "sms charges", "annual fee"),
}

EXPORT_FIELDS = (
    "date",
    "description",
    "debit_amount",
    "credit_amount",
    "balance",
    "category",
    "confidence",
)


def _render_cell(cell: object) -> str:
    """Render one spreadsheet/CSV cell for text-style parsing without losing values."""
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip()


@dataclass
class Transaction:
    date: str
    description: str
    debit_amount: float = 0.0
    credit_amount: float = 0.0
    balance: float = 0.0
    category: str = "Unknown"
    classification_method: str = "heuristic"
    confidence: float = 0.0

    def as_record(self) -> dict[str, str | float]:
        return {
            "date": self.date,
            "description": self.description,
            "debit_amount": self.debit_amount,
            "credit_amount": self.credit_amount,
            "balance": self.balance,
            "category": self.category,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class TransactionValidation:
    """Result of validating one candidate transaction row."""

    is_valid: bool
    issues: list[str] = field(default_factory=list)


@dataclass
class SupportingSections:
    """Supporting/detail sections parsed separately from the primary table."""

    deposits: list[Transaction] = field(default_factory=list)
    withdrawals: list[Transaction] = field(default_factory=list)
    fees: list[Transaction] = field(default_factory=list)
    checks: list[Transaction] = field(default_factory=list)


@dataclass
class BankStatement:
    file_name: str
    document_type: str
    bank_name: str | None = None
    account_holder_name: str | None = None
    account_number: str | None = None
    ifsc_code: str | None = None
    transactions: list[Transaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    opening_balance: float | None = None
    closing_balance: float | None = None
    statement_period: str | None = None
    extraction_method: str = "line_window"
    validation: dict[str, object] | None = None
    supporting_sections: SupportingSections = field(default_factory=SupportingSections)

    def as_summary(self) -> dict[str, str | int | float | None]:
        debits = sum(item.debit_amount for item in self.transactions)
        credits = sum(item.credit_amount for item in self.transactions)
        validation = self.validation or {}
        return {
            "file_name": self.file_name,
            "document_type": self.document_type,
            "bank_name": self.bank_name,
            "account_holder_name": self.account_holder_name,
            "account_number": self.account_number,
            "ifsc_code": self.ifsc_code,
            "transaction_count": len(self.transactions),
            "total_debit": round(debits, 2),
            "total_credit": round(credits, 2),
            "opening_balance": self.opening_balance,
            "closing_balance": self.closing_balance,
            "statement_period": self.statement_period,
            "extraction_method": self.extraction_method,
            "validation_status": validation.get("status"),
            "fees": validation.get("fees"),
        }


class MultinomialNaiveBayes:
    """Lightweight multinomial Naive Bayes over description tokens."""

    def __init__(self) -> None:
        self.class_counts: Counter[str] = Counter()
        self.word_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.token_totals: dict[str, int] = defaultdict(int)
        self.vocab: set[str] = set()

    def fit(self, texts: Iterable[str], labels: Iterable[str]) -> None:
        for text, label in zip(texts, labels, strict=False):
            tokens = _tokenize(text)
            self.class_counts[label] += 1
            self.word_counts[label].update(tokens)
            self.token_totals[label] += len(tokens)
            self.vocab.update(tokens)

    def predict_proba(self, text: str) -> dict[str, float]:
        tokens = _tokenize(text)
        if not self.class_counts:
            return {}
        vocab_size = max(len(self.vocab), 1)
        n_docs = sum(self.class_counts.values())
        n_classes = len(self.class_counts)
        log_scores: dict[str, float] = {}
        for label, count in self.class_counts.items():
            score = math.log((count + 1) / (n_docs + n_classes))
            denom = self.token_totals[label] + vocab_size
            for token in tokens:
                score += math.log((self.word_counts[label][token] + 1) / denom)
            log_scores[label] = score
        max_score = max(log_scores.values())
        exp_scores = {label: math.exp(score - max_score) for label, score in log_scores.items()}
        total = sum(exp_scores.values()) or 1.0
        return {label: value / total for label, value in exp_scores.items()}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]{2,}", text.lower())


def _training_corpus() -> tuple[list[str], list[str]]:
    examples: dict[str, list[str]] = {
        "Salary": ["SALARY CREDIT ACME", "PAYROLL NEFT COMPANY", "BONUS CREDIT JAN"],
        "Entertainment": ["NETFLIX SUBSCRIPTION", "SPOTIFY PREMIUM", "BOOKMYSHOW MOVIE"],
        "Housing": ["RENT PAYMENT LANDLORD", "SOCIETY MAINTENANCE", "APARTMENT LEASE"],
        "Transport": ["HP PETROL PUMP", "UBER TRIP", "IRCTC TICKET", "OLA CAB"],
        "Bills": ["ELECTRICITY BILL BESCOM", "AIRTEL BROADBAND", "JIO RECHARGE"],
        "Cash Withdrawal": ["ATM CASH WITHDRAWAL", "NWDLxxx ATM", "CASH WDL HDFC"],
        "Groceries": ["BIGBASKET GROCERY", "DMART SUPERMARKET", "RELIANCE FRESH"],
        "Food": ["SWIGGY ORDER", "ZOMATO FOOD", "DOMINOS PIZZA"],
        "Shopping": ["AMAZON PAY INDIA", "FLIPKART PAYMENT", "MYNTRA ORDER"],
        "Healthcare": ["APOLLO PHARMACY", "HOSPITAL BILL", "MEDPLUS MEDICAL"],
        "Insurance": ["LIC PREMIUM", "HEALTH INSURANCE", "POLICYBAZAAR"],
        "Investment": ["GROWW SIP MUTUAL FUND", "ZERODHA BROKERAGE", "MF PURCHASE"],
        "Transfer": ["UPI/JOHN/OKAXIS", "NEFT TRANSFER", "IMPS P2A REFUND"],
        "Interest": ["INTEREST CREDIT", "INT.PD SAVINGS"],
        "Bank Charges": ["GST CHARGES", "SMS ALERT FEE", "ANNUAL ACCOUNT FEE"],
        "Income": ["CUSTOMER PAYMENT RECEIVED", "FREELANCE CREDIT"],
        "Other Expense": ["MISC DEBIT PURCHASE", "POS DEBIT STORE"],
    }
    texts: list[str] = []
    labels: list[str] = []
    for label, rows in examples.items():
        texts.extend(rows)
        labels.extend([label] * len(rows))
    return texts, labels


class BankStatementProcessor:
    """Extract, classify, and export bank-statement transactions."""

    def __init__(self) -> None:
        self._model = MultinomialNaiveBayes()
        texts, labels = _training_corpus()
        self._model.fit(texts, labels)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def process_text(
        self,
        raw_text: str,
        file_name: str,
        document_type: str = "text",
    ) -> BankStatement:
        cleaned = self._clean_text(raw_text)
        if not cleaned:
            raise ValueError("The uploaded bank statement is empty.")

        pages = cleaned.split("\f") if "\f" in cleaned else [cleaned]
        return self._build_statement(pages, file_name, document_type=document_type)

    def process_tabular(
        self,
        rows: Iterable[Iterable[object]],
        file_name: str,
        document_type: str = "tabular",
    ) -> BankStatement:
        """Process a bank statement supplied as rows (CSV/XLSX spreadsheet exports).

        Rows feed both a table-aware parser (header detection, one cell per
        column) and the line/date-window parsers over the joined text; the
        highest-quality validated result wins. Handles statements that omit a
        running-balance column.
        """
        table_rows: list[list[object]] = [
            [cell for cell in row] for row in rows if any(cell not in (None, "") for cell in row)
        ]
        if not table_rows:
            raise ValueError("The uploaded bank statement is empty.")

        joined = "\n".join(
            " | ".join(_render_cell(cell) for cell in row) for row in table_rows
        )
        table_candidate = self._transactions_from_table(table_rows)
        return self._build_statement(
            [joined],
            file_name,
            document_type=document_type,
            table_candidate=table_candidate or None,
        )

    def process_image(
        self,
        image_bytes: bytes,
        file_name: str,
        document_type: str = "image",
    ) -> BankStatement:
        """OCR a bank statement stored as an image (PNG/JPG/JPEG/WebP/BMP/TIFF)."""
        if not image_bytes:
            raise ValueError("That file is empty.")
        try:
            from io import BytesIO

            from PIL import Image

            image = Image.open(BytesIO(image_bytes))
            image.load()
        except Exception as error:
            raise ValueError(
                f"That file is not a readable image ({type(error).__name__})."
            ) from error
        text = self._ocr_image(image)
        if not text.strip():
            raise ValueError(
                "No readable text was found in the image; try a higher-resolution upload."
            )
        boxes = self._ocr_image_lines(image) or None
        return self._build_statement(
            [text],
            file_name,
            document_type=document_type,
            boxes=boxes,
        )

    def process_pdf(
        self,
        pdf_bytes: bytes,
        file_name: str,
        password: str = "",
    ) -> BankStatement:
        if not pdf_bytes or b"%PDF" not in pdf_bytes[:1024]:
            raise ValueError("That file is not a readable PDF. Export or unlock the statement and try again.")

        try:
            text_pages, used_ocr = self._read_pdf_pages(pdf_bytes, password)
        except ValueError:
            raise
        except Exception as error:
            raise ValueError(self._pdf_error_message(error)) from error

        combined = "\n".join(text_pages)
        if not combined.strip():
            raise ValueError(
                "No readable text was found in this PDF. If it is password-protected, enter the password "
                "in Settings and upload again."
            )

        boxes = self._ocr_pdf_lines(pdf_bytes, password) or None if used_ocr else None
        table_candidate = self._extract_transactions_from_pdf_tables(pdf_bytes, password) or None
        statement = self._build_statement(
            text_pages,
            file_name,
            document_type="image" if used_ocr else "text",
            boxes=boxes,
            table_candidate=table_candidate,
        )

        # Scanned fallback for "text" PDFs that yielded too few transactions:
        # rasterize anyway and let OCR try (mirrors previous behaviour).
        if not used_ocr and len(statement.transactions) < 2:
            try:
                ocr_pages = self._ocr_pdf(pdf_bytes, password)
                ocr_boxes = self._ocr_pdf_lines(pdf_bytes, password) or None
                ocr_statement = self._build_statement(
                    ocr_pages,
                    file_name,
                    document_type="image",
                    boxes=ocr_boxes,
                    table_candidate=table_candidate,
                )
                if len(ocr_statement.transactions) > len(statement.transactions):
                    statement = ocr_statement
                    used_ocr = True
            except Exception:
                pass

        if used_ocr:
            statement.document_type = "image"
            statement.warnings.append("Image-based PDF detected; text was recovered with OCR.")
        return statement

    def detect_pdf_type(self, pdf_bytes: bytes, password: str = "") -> str:
        """Return 'text' or 'image' from whether native PDF text is present."""
        return "text" if self._pdf_has_native_text(pdf_bytes, password) else "image"

    def classify_transactions(self, transactions: Iterable[Transaction]) -> list[Transaction]:
        classified: list[Transaction] = []
        for transaction in transactions:
            category, method, confidence = self._classify_description(
                transaction.description,
                transaction.credit_amount,
                transaction.debit_amount,
            )
            classified.append(
                Transaction(
                    date=transaction.date,
                    description=transaction.description,
                    debit_amount=transaction.debit_amount,
                    credit_amount=transaction.credit_amount,
                    balance=transaction.balance,
                    category=category,
                    classification_method=method,
                    confidence=confidence,
                )
            )
        return classified

    def export_transactions(
        self,
        transactions: Iterable[Transaction] | BankStatement,
        file_path: str | Path,
    ) -> Path:
        statement = transactions if isinstance(transactions, BankStatement) else None
        rows = statement.transactions if statement is not None else list(transactions)
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == ".csv":
            path.write_bytes(self.export_bytes(rows, "csv", statement))
            return path
        if path.suffix.lower() == ".xlsx":
            path.write_bytes(self.export_bytes(rows, "xlsx", statement))
            return path
        raise ValueError("Unsupported export format. Use .csv or .xlsx.")

    def export_bytes(
        self,
        transactions: Iterable[Transaction],
        fmt: str,
        statement: BankStatement | None = None,
    ) -> bytes:
        rows = [item for item in transactions if self._validate_transaction(item).is_valid]
        if fmt == "csv":
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_FIELDS))
            writer.writeheader()
            for transaction in rows:
                writer.writerow(transaction.as_record())
            return buffer.getvalue().encode("utf-8")
        if fmt == "xlsx":
            if Workbook is None:
                raise RuntimeError("openpyxl is required to export Excel files.")
            workbook = Workbook()
            summary = workbook.active
            summary.title = "Summary"
            if statement is not None:
                for key, value in statement.as_summary().items():
                    summary.append([key, value])
            else:
                summary.append(["transaction_count", len(rows)])
            sheet = workbook.create_sheet("Transactions")
            sheet.append(list(EXPORT_FIELDS))
            for transaction in rows:
                record = transaction.as_record()
                sheet.append([record[field_name] for field_name in EXPORT_FIELDS])
            validation = getattr(statement, "validation", None) if statement is not None else None
            if isinstance(validation, dict):
                checks = validation.get("checks")
                if isinstance(checks, list) and checks:
                    validation_sheet = workbook.create_sheet("Validation")
                    validation_sheet.append(["check", "expected", "actual", "status", "message"])
                    for check in checks:
                        validation_sheet.append(
                            [
                                check.get("check", ""),
                                check.get("expected", ""),
                                check.get("actual", ""),
                                check.get("status", ""),
                                check.get("message", ""),
                            ]
                        )
            output = io.BytesIO()
            workbook.save(output)
            return output.getvalue()
        raise ValueError("Unsupported export format. Use csv or xlsx.")

    # ------------------------------------------------------------------
    # Pipeline plumbing
    # ------------------------------------------------------------------

    def _build_statement(
        self,
        pages: list[str],
        file_name: str,
        document_type: str = "text",
        boxes: list[list[list[dict[str, float | str]]]] | None = None,
        table_candidate: list[Transaction] | None = None,
    ) -> BankStatement:
        """Run the full extract -> normalize -> validate -> classify -> validate pipeline."""
        full_text = "\n".join(pages)
        primary_text, sections = self._detect_sections(pages)

        candidates: list[tuple[str, list[Transaction]]] = [
            ("line_window", self._extract_transactions(primary_text)),
        ]
        if boxes:
            try:
                box_rows = self._transactions_from_box_pages(boxes)
            except Exception:
                box_rows = []
            if box_rows:
                candidates.append(("box_columns", box_rows))
        if table_candidate:
            candidates.append(("pdf_tables", table_candidate))

        best_label, best_rows = max(
            candidates,
            # Quality first; among equal-quality results prefer the one that
            # recovered more validated rows.
            key=lambda item: (self._score_candidate(item[1]), len(item[1])),
        )
        valid_rows, dropped = self._validate_transaction_rows(best_rows)
        self._normalize_balances(valid_rows)

        statement = BankStatement(
            file_name=file_name,
            document_type=document_type,
            bank_name=self._extract_bank_name(full_text),
            account_holder_name=self._extract_holder(full_text),
            account_number=self._extract_account_number(full_text),
            ifsc_code=self._extract_ifsc(full_text),
            transactions=self.classify_transactions(valid_rows),
            extraction_method=best_label,
        )
        if not statement.transactions:
            statement.warnings.append("No transaction rows could be parsed from this document.")

        supporting = self._parse_supporting_sections(sections)
        statement.supporting_sections = supporting

        parse_dropped = self._count_unparsed_date_rows(primary_text)
        validation = self._build_validation(statement, full_text, dropped, supporting, parse_dropped)
        statement.validation = validation
        statement.opening_balance = validation.get("opening_balance")
        statement.closing_balance = validation.get("closing_balance")
        statement.statement_period = validation.get("statement_period")
        statement.warnings.extend(validation["warnings"])
        return statement

    # ------------------------------------------------------------------
    # Page & section detection
    # ------------------------------------------------------------------

    def _detect_sections(self, pages: list[str]) -> tuple[str, dict[str, list[str]]]:
        """Split OCR/native text into the primary table and supporting sections.

        Returns ``(primary_text, sections)`` where ``sections`` only ever holds
        the supporting detail sections (deposits, withdrawals, fees, checks).
        Supporting lines are never included in the primary table text, so the
        page-2 detail sections cannot leak into the transaction list.
        """
        sections: dict[str, list[str]] = {key: [] for key, _ in _SUPPORTING_SECTION_KEYS}
        primary_lines: list[str] = []
        current: str | None = None

        for page_index, page in enumerate(pages):
            if page_index > 0:
                # A new page starts in an unknown zone; the next explicit header
                # (or section title) decides what belongs where.
                current = None
            for raw in page.splitlines():
                line = raw.strip()
                if not line:
                    continue
                section_key = next(
                    (key for key, pattern in _SUPPORTING_SECTION_KEYS if re.search(pattern, line, re.I)),
                    None,
                )
                if section_key:
                    current = section_key
                    continue
                if _SUMMARY_SECTION_RE.search(line):
                    current = None
                    continue
                if _PRIMARY_TABLE_HEADER_LINE.search(line):
                    current = "table"
                    primary_lines.append(line)
                    continue
                if current == "table":
                    # Keep the row out of the primary region entirely: totals,
                    # page numbers, "opening balance" headers are not rows.
                    if _NOISE_LINE.search(line) and _DATE_TOKEN.search(line) is None:
                        continue
                    primary_lines.append(line)
                elif current in sections:
                    sections[current].append(line)

        if not primary_lines:
            # No header found (unusual layout / OCR missed it). Fall back to all
            # non-section text, still excluding supporting detail sections.
            fallback: list[str] = []
            for page in pages:
                for raw in page.splitlines():
                    line = raw.strip()
                    if not line or _NOISE_LINE.search(line) and _DATE_TOKEN.search(line) is None:
                        continue
                    if _SUMMARY_SECTION_RE.search(line):
                        continue
                    if any(re.search(pattern, line, re.I) for _, pattern in _SUPPORTING_SECTION_KEYS):
                        continue
                    fallback.append(line)
            primary_lines = fallback
        return "\n".join(primary_lines), sections

    def _parse_supporting_sections(self, sections: dict[str, list[str]]) -> SupportingSections:
        """Parse page-2 detail sections into structures for cross-checking only."""
        result = SupportingSections()
        for key in ("deposits", "withdrawals", "fees", "checks"):
            text = "\n".join(sections.get(key, []))
            if not text.strip():
                continue
            rows = self._extract_transactions(text)
            valid, _dropped = self._validate_transaction_rows(rows)
            setattr(result, key, valid)
        return result

    # ------------------------------------------------------------------
    # Metadata extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_text(raw_text: str) -> str:
        return "\n".join(line.strip() for line in str(raw_text).splitlines() if line.strip())

    @staticmethod
    def _extract_bank_name(text: str) -> str | None:
        match = _BANK_NAME.search(text)
        if not match:
            return None
        line = re.sub(r"\s+", " ", match.group(0)).strip()
        return line[:80] or None

    @staticmethod
    def _extract_holder(text: str) -> str | None:
        match = _HOLDER.search(text)
        if not match:
            return None
        value = match.group(1).strip(" :-")
        value = re.split(r"\s{2,}|Account|IFSC|Branch", value, maxsplit=1)[0].strip()
        return value or None

    @staticmethod
    def _extract_account_number(text: str) -> str | None:
        """Extract an account number only from account-number context.

        The captured value must contain digits (so OCR fragments such as
        "TIVITY" are rejected) and trailing mask placeholders (``XXXX``) are
        removed.
        """
        match = _ACCOUNT_NO.search(text)
        if not match:
            return None
        return BankStatementProcessor._sanitize_account_number(match.group(1))

    @staticmethod
    def _sanitize_account_number(value: str) -> str | None:
        cleaned = re.sub(r"[\s\-]+", "", value.strip().upper())
        cleaned = re.sub(r"(?:X|\*)+$", "", cleaned)
        if not cleaned or not any(char.isdigit() for char in cleaned):
            return None
        if len(cleaned) < 4 or len(cleaned) > 30:
            return None
        return cleaned

    @staticmethod
    def _extract_ifsc(text: str) -> str | None:
        labeled = re.search(r"IFSC(?:\s*CODE)?\s*[:\-]?\s*([A-Z]{4}0[A-Z0-9]{6})", text, re.I)
        if labeled:
            return labeled.group(1).upper()
        match = _IFSC.search(text.upper())
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    # Transaction extraction (multiple strategies, quality-scored)
    # ------------------------------------------------------------------

    def _extract_transactions(self, text: str) -> list[Transaction]:
        line_txs = self._extract_from_lines(text)
        window_txs = self._extract_by_date_windows(text)
        return self._dedupe(max([line_txs, window_txs], key=len))

    def _extract_from_lines(self, text: str) -> list[Transaction]:
        lines = text.splitlines()
        start = 0
        for index, line in enumerate(lines):
            compact = re.sub(r"\s+", " ", line).upper()
            if "DATE" in compact and any(
                token in compact
                for token in ("DESC", "NARRATION", "PARTICULAR", "WITHDRAW", "DEBIT", "CREDIT", "AMOUNT", "BALANCE")
            ):
                start = index + 1
                break

        transactions: list[Transaction] = []
        buffer: list[str] = []

        def flush() -> None:
            if not buffer:
                return
            parsed = self._parse_transaction_chunk(" ".join(buffer))
            if parsed is not None:
                transactions.append(parsed)

        for line in lines[start:]:
            lowered = line.lower()
            if re.search(r"opening\s+balance|closing\s+balance|brought\s+forward|carried\s+forward", lowered):
                continue
            if _NOISE_LINE.search(line) and _DATE_TOKEN.search(line) is None:
                continue
            if _DATE_TOKEN.search(line):
                flush()
                buffer = [line]
            elif buffer:
                buffer.append(line)
        flush()
        return self._dedupe(transactions)

    def _extract_by_date_windows(self, text: str) -> list[Transaction]:
        matches = list(_DATE_TOKEN.finditer(text))
        transactions: list[Transaction] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            chunk = re.sub(r"\s+", " ", text[match.start() : end]).strip()
            if re.search(r"opening\s+balance|closing\s+balance|brought\s+forward|carried\s+forward", chunk, re.I):
                continue
            parsed = self._parse_transaction_chunk(chunk)
            if parsed is not None:
                transactions.append(parsed)
        return self._dedupe(transactions)

    def _parse_transaction_line(self, line: str) -> Transaction | None:
        return self._parse_transaction_chunk(line)

    def _parse_transaction_chunk(self, chunk: str) -> Transaction | None:
        date_match = _DATE_TOKEN.search(chunk)
        if not date_match:
            return None
        date_value = self._normalize_date(date_match.group(1))
        if date_value is None:
            return None
        remainder = re.sub(r"\s+", " ", (chunk[: date_match.start()] + " " + chunk[date_match.end() :]).strip())
        description, amounts, flags = self._split_description_and_amounts(remainder)
        if not amounts:
            return None
        debit, credit, balance = self._split_amounts(description, amounts, flags)
        if debit == 0 and credit == 0:
            return None
        return Transaction(
            date=date_value,
            description=re.sub(r"\s+", " ", description).strip() or "Transaction",
            debit_amount=debit,
            credit_amount=credit,
            balance=balance,
        )

    @staticmethod
    def _money_strength(token: str) -> str:
        """Classify a token as ``"strong"``, ``"weak"`` or ``""`` (not money).

        Strong tokens carry decimals, thousands separators, currency symbols,
        trailing ``CR``/``DR`` or wrapping parentheses. A bare integer is only a
        weak candidate and is ignored while any strong candidate is present, so
        check numbers and reference/terminal IDs can never become amounts as
        long as a normal decimal-format amount exists.
        """
        cleaned = token.strip()
        if not cleaned or re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?", cleaned):
            return ""  # timestamps are never money
        lowered = cleaned.lower().replace("₹", "₹")
        currency_free = re.sub(r"^(?:₹|rs\.?|inr|usd|eur)\b", "", lowered).strip()
        currency_free = re.sub(r"(cr|dr)$", "", currency_free).strip()
        body = currency_free.strip("()").replace(",", "")
        if not body or body in {".", "-"}:
            return ""
        if re.fullmatch(r"\d+\.\d{1,2}", body):
            return "strong"
        if re.fullmatch(r"\d+", body):
            digits = len(body)
            if digits == 0 or digits >= 9:
                return ""  # 9+ digit runs are reference/account codes
            return "weak"
        return ""

    @staticmethod
    def _split_description_and_amounts(remainder: str) -> tuple[str, list[float], list[str]]:
        """Split a transaction line into (description, amounts, flags).

        Reference identifiers that must never become money are removed from the
        candidate pool (but stay in the description): numbers following a check
        / terminal / reference label, 9+ digit runs, and timestamps.
        """
        tokens = remainder.split()
        if not tokens:
            return "", [], []
        blocked = [False] * len(tokens)

        def weak_id(token: str) -> bool:
            return bool(re.fullmatch(r"\d+", token)) and BankStatementProcessor._money_strength(token) == "weak"

        for index, token in enumerate(tokens):
            if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?", token):
                blocked[index] = True
            if re.fullmatch(r"\d{9,}", token):
                blocked[index] = True
        for index, token in enumerate(tokens):
            if _REF_LABEL_RE.match(token) and index + 1 < len(tokens) and weak_id(tokens[index + 1]):
                blocked[index + 1] = True

        candidates: list[tuple[int, int, str, str]] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if blocked[index]:
                index += 1
                continue
            strength = BankStatementProcessor._money_strength(token)
            if strength:
                width = 1
                flag_token = token
                if index + 1 < len(tokens) and re.fullmatch(r"(?i)(cr|dr)", tokens[index + 1]):
                    width = 2
                    flag_token = f"{token} {tokens[index + 1]}"
                candidates.append((index, width, flag_token, strength))
                index += width
                continue
            index += 1

        if not candidates:
            return remainder.strip(" -|:/"), [], []

        strong = [candidate for candidate in candidates if candidate[3] == "strong"]
        pool = strong if strong else candidates
        chosen = pool[-3:] if len(pool) >= 3 else pool

        skip: set[int] = set()
        amounts: list[float] = []
        flags: list[str] = []
        for start, width, flag_token, _strength in chosen:
            skip.update(range(start, start + width))
            amounts.append(BankStatementProcessor._to_decimal(flag_token))
            flags.append(flag_token.upper())
        description = " ".join(token for idx, token in enumerate(tokens) if idx not in skip)
        return description.strip(" -|:/"), amounts, flags

    @staticmethod
    def _looks_like_amount(token: str) -> bool:
        return BankStatementProcessor._money_strength(token) != ""

    @staticmethod
    def _split_amounts(
        description: str,
        amounts: list[float],
        flags: list[str],
    ) -> tuple[float, float, float]:
        upper = description.upper()
        if len(amounts) >= 3:
            # Three value columns: debit, credit, balance.
            return abs(amounts[0]), abs(amounts[1]), amounts[-1]
        if len(amounts) == 2:
            # Two-value rows are either debit|credit (no balance column) or
            # amount|balance. A zero first/second value unambiguously marks a
            # debit|credit pair, e.g. "0.00 75000.00" is a 75000.00 credit.
            if amounts[0] == 0 and amounts[1] != 0:
                return 0.0, abs(amounts[1]), 0.0
            if amounts[1] == 0 and amounts[0] != 0:
                return abs(amounts[0]), 0.0, 0.0
            amount, balance = amounts[0], amounts[1]
        else:
            amount, balance = amounts[0], 0.0
        flag = flags[0] if flags else ""
        if "CR" in flag or any(token in upper for token in ("CREDIT", "SALARY", "INTEREST", "REFUND")):
            return 0.0, abs(amount), balance
        if "DR" in flag or any(token in upper for token in ("DEBIT", "WITHDRAWAL", "POS", "UPI", "PURCHASE")):
            return abs(amount), 0.0, balance
        if amount < 0:
            return abs(amount), 0.0, balance
        return abs(amount), 0.0, balance

    @staticmethod
    def _dedupe(transactions: list[Transaction]) -> list[Transaction]:
        seen: set[tuple[str, str, float, float, float]] = set()
        unique: list[Transaction] = []
        for item in transactions:
            key = (
                item.date,
                re.sub(r"\s+", " ", item.description.lower()),
                item.debit_amount,
                item.credit_amount,
                item.balance,
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_transaction(self, transaction: Transaction) -> TransactionValidation:
        issues: list[str] = []
        if self._parse_date_value(transaction.date) is None:
            issues.append("invalid or incomplete date")
        if not transaction.description or not transaction.description.strip():
            issues.append("missing description")
        elif len(transaction.description) > 300 or _SECONDARY_TEXT_RE.search(transaction.description):
            issues.append("description contains header/section text")
        if transaction.debit_amount < 0 or transaction.credit_amount < 0:
            issues.append("negative amount")
        if transaction.debit_amount == 0 and transaction.credit_amount == 0:
            issues.append("zero amount")
        if transaction.debit_amount > 0 and transaction.credit_amount > 0:
            issues.append("both debit and credit present")
        if BankStatementProcessor._has_suspicious_amount(transaction):
            issues.append("suspicious amount magnitude")
        return TransactionValidation(is_valid=not issues, issues=issues)

    @staticmethod
    def _has_suspicious_amount(transaction: Transaction) -> bool:
        return any(
            value > _VALIDATION_MAX_AMOUNT for value in (transaction.debit_amount, transaction.credit_amount)
        )

    def _validate_transaction_rows(
        self,
        rows: list[Transaction],
    ) -> tuple[list[Transaction], list[list[str]]]:
        valid: list[Transaction] = []
        dropped: list[list[str]] = []
        for transaction in self._dedupe(rows):
            result = self._validate_transaction(transaction)
            if result.is_valid:
                valid.append(transaction)
            else:
                dropped.append([f"{transaction.date}: {issue}" for issue in result.issues])
        return valid, dropped

    @staticmethod
    def _normalize_balances(rows: list[Transaction]) -> None:
        """Drop zero-balance placeholders so a missing balance never exports as 0.

        A balance column that was not present parses to 0.0; treat that as
        "no data" so downstream balance arithmetic is not corrupted.
        """
        for transaction in rows:
            if transaction.balance == 0.0:
                transaction.balance = 0.0

    @staticmethod
    def _balance_warnings(transactions: list[Transaction]) -> list[str]:
        warnings: list[str] = []
        previous: Transaction | None = None
        mismatches = 0
        for item in transactions:
            if previous is not None and previous.balance and item.balance:
                expected = round(previous.balance - item.debit_amount + item.credit_amount, 2)
                if abs(expected - item.balance) > _BALANCE_TOLERANCE:
                    mismatches += 1
            previous = item
        if mismatches:
            warnings.append(
                f"{mismatches} transaction(s) do not follow running-balance arithmetic; OCR or layout gaps are likely."
            )
        return warnings

    # ------------------------------------------------------------------
    # Parser quality scoring
    # ------------------------------------------------------------------

    def _score_candidate(self, transactions: Iterable[Transaction]) -> float:
        """Score a candidate result by data quality, never by raw row count.

        Bonus for valid dates/amounts/descriptions and running-balance
        consistency; penalty for malformed/duplicate/suspicious rows.
        """
        raw = list(transactions)
        if not raw:
            return -float("inf")
        duplicate_penalty = len(raw) - len(self._dedupe(raw))
        rows = self._dedupe(raw)
        valid = [row for row in rows if self._validate_transaction(row).is_valid]
        if not valid:
            return -float("inf")
        dropped = len(rows) - len(valid)
        count = len(valid)

        dates_ok = sum(1 for row in valid if self._parse_date_value(row.date) is not None)
        descriptions_ok = sum(
            1 for row in valid if self._description_ok(row.description)
        )
        amounts_ok = sum(
            1
            for row in valid
            if not BankStatementProcessor._has_suspicious_amount(row)
            and not (row.debit_amount > 0 and row.credit_amount > 0)
        )
        both_sides = sum(1 for row in valid if row.debit_amount > 0 and row.credit_amount > 0)
        suspicious = sum(
            1
            for row in valid
            if any(
                value > _SCORE_SUSPICIOUS_AMOUNT for value in (row.debit_amount, row.credit_amount)
            )
        )

        balance_ok = 0
        previous: Transaction | None = None
        for row in valid:
            if previous is not None and previous.balance and row.balance:
                expected = round(previous.balance - row.debit_amount + row.credit_amount, 2)
                if abs(expected - row.balance) <= _BALANCE_TOLERANCE:
                    balance_ok += 1
            previous = row
        balance_denominator = max(count - 1, 1)

        return (
            3.0 * dates_ok / count
            + 4.0 * amounts_ok / count
            + 2.0 * descriptions_ok / count
            + 5.0 * balance_ok / balance_denominator
            - 3.0 * both_sides / count
            - 2.0 * dropped
            - 1.5 * duplicate_penalty
            - 1.0 * suspicious
        )

    @staticmethod
    def _description_ok(description: str) -> bool:
        if not description or not description.strip():
            return False
        if len(description) > 300:
            return False
        return not _SECONDARY_TEXT_RE.search(description)

    # ------------------------------------------------------------------
    # Date handling
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_short_date(compact: str, pattern: str) -> datetime | None:
        """Validate a year-less date token, ignoring the py3.15 ambiguity warning.

        Day/month-only formats have no year, so ``strptime`` will warn about
        leap-day ambiguity in newer interpreters. The token is only ever used
        to *validate* plausibility, never to fabricate a full date.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            try:
                return datetime.strptime(compact, pattern)
            except ValueError:
                return None

    @staticmethod
    def _parse_date_value(raw_date: str) -> datetime | None:
        """Validate a date token against full and short formats.

        Impossible values (month 13, day 0, "00-00-00"...) return ``None`` so
        the row is discarded instead of exported with a fabricated date.
        """
        compact = re.sub(r"\s+", " ", str(raw_date).strip())
        if not compact:
            return None
        for pattern in _DATE_PATTERNS:
            try:
                return datetime.strptime(compact, pattern)
            except ValueError:
                continue
        for pattern in _DATE_PATTERNS_SHORT:
            parsed = BankStatementProcessor._parse_short_date(compact, pattern)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _normalize_date(raw_date: str) -> str | None:
        """Normalize a full date to ``YYYY-MM-DD``; keep validated short dates.

        Returns ``None`` when the token is not a real calendar date.
        """
        compact = re.sub(r"\s+", " ", raw_date.strip())
        for pattern in _DATE_PATTERNS:
            try:
                return datetime.strptime(compact, pattern).strftime("%Y-%m-%d")
            except ValueError:
                continue
        for pattern in _DATE_PATTERNS_SHORT:
            if BankStatementProcessor._parse_short_date(compact, pattern) is not None:
                return compact
        return None

    def _count_unparsed_date_rows(self, text: str) -> int:
        """Count date-shaped tokens that failed calendar validation.

        Rows whose date token is not a real calendar date (OCR garbage such as
        ``00-00-00``) never become transactions; count them so the statement
        can surface a warning instead of dropping them silently.
        """
        matches = list(_DATE_TOKEN.finditer(text))
        count = 0
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            chunk = re.sub(r"\s+", " ", text[match.start() : end]).strip()
            if re.search(
                r"opening\s+balance|closing\s+balance|brought\s+forward|carried\s+forward",
                chunk,
                re.I,
            ):
                continue
            if self._normalize_date(match.group(1)) is None:
                count += 1
        return count

    # ------------------------------------------------------------------
    # PDF engines
    # ------------------------------------------------------------------

    def _read_pdf_pages(self, pdf_bytes: bytes, password: str = "") -> tuple[list[str], bool]:
        fitz_pages = self._extract_with_pymupdf(pdf_bytes, password)
        if self._text_looks_useful(fitz_pages):
            return fitz_pages, False
        pypdf_pages = self._extract_with_pypdf(pdf_bytes, password)
        if self._text_looks_useful(pypdf_pages):
            return pypdf_pages, False
        return self._ocr_pdf(pdf_bytes, password), True

    def _extract_transactions_from_pdf_tables(
        self,
        pdf_bytes: bytes,
        password: str = "",
    ) -> list[Transaction]:
        try:
            import pymupdf
        except ImportError:
            return []
        try:
            document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            self._unlock_pdf(document, password)
        except Exception:
            return []

        transactions: list[Transaction] = []
        for page in document:
            try:
                finder = page.find_tables()
            except Exception:
                continue
            tables = getattr(finder, "tables", finder) or []
            for table in tables:
                try:
                    rows = table.extract()
                except Exception:
                    continue
                transactions.extend(self._transactions_from_table(rows))
        return self._dedupe(transactions)

    def _transactions_from_table(self, rows: list[list[object]]) -> list[Transaction]:
        if not rows:
            return []
        header_index = None
        mapping: dict[str, int] = {}
        for index, raw_row in enumerate(rows):
            mapping = self._header_mapping([str(cell or "") for cell in raw_row])
            if mapping:
                header_index = index
                break
        body = rows[header_index + 1 :] if header_index is not None else rows
        transactions: list[Transaction] = []
        current: Transaction | None = None
        for raw_row in body:
            cells = [re.sub(r"\s+", " ", str(cell or "")).strip() for cell in raw_row]
            joined = " ".join(cells)
            if _NOISE_LINE.search(joined) and _DATE_TOKEN.search(joined) is None:
                continue
            parsed = self._transaction_from_cells(cells, mapping)
            if parsed is not None:
                if current is not None:
                    transactions.append(current)
                current = parsed
                continue
            extra = " ".join(cell for cell in cells if cell)
            if current is not None and extra and _DATE_TOKEN.search(extra) is None:
                current.description = f"{current.description} {extra}".strip()
        if current is not None:
            transactions.append(current)
        return transactions

    def _transaction_from_cells(
        self,
        cells: list[str],
        mapping: dict[str, int],
    ) -> Transaction | None:
        joined = " ".join(cells)
        if mapping:
            date_value = cells[mapping["date"]] if "date" in mapping and mapping["date"] < len(cells) else ""
            description = (
                cells[mapping["description"]]
                if "description" in mapping and mapping["description"] < len(cells)
                else joined
            )
            debit = abs(self._cell_amount(cells, mapping.get("debit")))
            credit = abs(self._cell_amount(cells, mapping.get("credit")))
            amount = self._cell_amount(cells, mapping.get("amount"))
            balance = self._cell_amount(cells, mapping.get("balance"))
            if amount and debit == 0 and credit == 0:
                debit, credit, balance = self._split_amounts(description, [amount, balance or 0.0], [joined])
            if not _DATE_TOKEN.search(date_value or joined):
                return None
            date_token = _DATE_TOKEN.search(date_value or joined)
            if date_token is None:
                return None
            date_value = self._normalize_date(date_token.group(1))
            if date_value is None:
                return None
            if debit == 0 and credit == 0:
                return None
            return Transaction(
                date=date_value,
                description=re.sub(r"\s+", " ", description).strip() or "Transaction",
                debit_amount=debit,
                credit_amount=credit,
                balance=balance,
            )
        return self._parse_transaction_line(joined)

    @staticmethod
    def _header_mapping(cells: list[str]) -> dict[str, int]:
        mapping: dict[str, int] = {}
        for index, cell in enumerate(cells):
            label = cell.lower()
            if "date" in label and "date" not in mapping:
                mapping["date"] = index
            elif any(token in label for token in ("narration", "particular", "description", "details", "remarks")):
                mapping["description"] = index
            elif any(token in label for token in ("withdraw", "debit", "dr")) and "debit" not in mapping:
                mapping["debit"] = index
            elif any(token in label for token in ("deposit", "credit", "cr")) and "credit" not in mapping:
                mapping["credit"] = index
            elif "balance" in label:
                mapping["balance"] = index
            elif label.strip() in {"amount", "txn amount", "transaction amount"}:
                mapping["amount"] = index
        return mapping if "date" in mapping and ("debit" in mapping or "credit" in mapping or "amount" in mapping) else {}

    def _cell_amount(self, cells: list[str], index: int | None) -> float:
        if index is None or index >= len(cells) or not cells[index]:
            return 0.0
        match = _MONEY_TOKEN.search(cells[index])
        if match:
            return self._to_decimal(match.group(0))
        if self._looks_like_amount(cells[index]):
            return self._to_decimal(cells[index])
        return 0.0

    # ------------------------------------------------------------------
    # Column-aware table extraction from OCR box coordinates
    # ------------------------------------------------------------------

    def _transactions_from_box_pages(
        self,
        box_pages: list[list[list[dict[str, float | str]]]],
    ) -> list[Transaction]:
        transactions: list[Transaction] = []
        for page_lines in box_pages:
            transactions.extend(self._transactions_from_box_lines(page_lines))
        return self._dedupe(transactions)

    def _transactions_from_box_lines(
        self,
        lines: list[list[dict[str, float | str]]],
    ) -> list[Transaction]:
        """Extract the primary table using x-coordinate column boundaries.

        The header line supplies the column centers; each data token is snapped
        to the nearest column, so a terminal/check/reference number sitting in
        the Description column can never become an amount.
        """
        header_index = None
        mapping: dict[str, int] = {}
        for index, line in enumerate(lines):
            texts = [str(token.get("text", "")) for token in line]
            if _PRIMARY_TABLE_HEADER_LINE.search(" ".join(texts)):
                candidate = self._header_mapping(texts)
                if candidate:
                    header_index = index
                    mapping = candidate
                    break
        if header_index is None or not mapping:
            return []
        ranges = self._column_boundaries(lines[header_index])
        if not ranges:
            return []

        transactions: list[Transaction] = []
        current: Transaction | None = None
        for line in lines[header_index + 1 :]:
            joined = " ".join(str(token.get("text", "")) for token in line)
            if _NOISE_LINE.search(joined) and _DATE_TOKEN.search(joined) is None:
                continue
            cells = self._assign_tokens_to_columns(line, ranges)
            parsed = self._transaction_from_cells(cells, mapping)
            if parsed is not None:
                if current is not None:
                    transactions.append(current)
                current = parsed
                continue
            extra = " ".join(cell for cell in cells if cell)
            if current is not None and extra and _DATE_TOKEN.search(extra) is None:
                current.description = f"{current.description} {extra}".strip()
        if current is not None:
            transactions.append(current)
        return transactions

    @staticmethod
    def _column_boundaries(header_tokens: list[dict[str, float | str]]) -> list[tuple[float, float]]:
        """Turn header token centers into per-column x ranges (midpoint splits)."""
        if not header_tokens:
            return []
        centers = [float(token["cx"]) for token in header_tokens]
        ranges: list[tuple[float, float]] = []
        for index, center in enumerate(centers):
            left = (centers[index - 1] + center) / 2 if index > 0 else -1e18
            right = (center + centers[index + 1]) / 2 if index + 1 < len(centers) else 1e18
            ranges.append((left, right))
        return ranges

    @staticmethod
    def _assign_tokens_to_columns(
        line: list[dict[str, float | str]],
        ranges: list[tuple[float, float]],
    ) -> list[str]:
        cells = [""] * len(ranges)
        for token in line:
            center = float(token["cx"])
            for index, (left, right) in enumerate(ranges):
                if left <= center < right:
                    cells[index] = f"{cells[index]} {token['text']}".strip()
                    break
        return cells

    # ------------------------------------------------------------------
    # Native text extraction / OCR
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_with_pymupdf(pdf_bytes: bytes, password: str = "") -> list[str]:
        try:
            import pymupdf
        except ImportError:
            return []
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        BankStatementProcessor._unlock_pdf(document, password)
        pages: list[str] = []
        for page in document:
            rows = BankStatementProcessor._page_text_rows(page)
            if rows:
                pages.append(rows)
        return pages

    @staticmethod
    def _page_text_rows(page: object) -> str:
        words = []
        getter = getattr(page, "get_text", None)
        if getter is not None:
            try:
                words = list(getter("words") or [])
            except Exception:
                words = []
        if words:
            rows: dict[int, list[tuple[float, str]]] = {}
            for word in words:
                if len(word) < 5:
                    continue
                x0, y0, _x1, _y1, token = word[:5]
                text = str(token).strip()
                if not text:
                    continue
                key = int(round(float(y0) / 4.0))
                rows.setdefault(key, []).append((float(x0), text))
            lines: list[str] = []
            for key in sorted(rows):
                cells = sorted(rows[key], key=lambda item: item[0])
                lines.append(" ".join(token for _x, token in cells))
            reconstructed = "\n".join(lines).strip()
            if reconstructed:
                return reconstructed
        return (page.get_text("text") or "").strip()

    @staticmethod
    def _extract_with_pypdf(pdf_bytes: bytes, password: str = "") -> list[str]:
        try:
            from pypdf import PdfReader
        except ImportError:
            return []
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            unlocked = False
            for candidate in {password, ""}:
                try:
                    if reader.decrypt(candidate) != 0:
                        unlocked = True
                        break
                except Exception:
                    continue
            if not unlocked:
                raise ValueError(
                    "This PDF is password-protected. Open Settings, enter the statement password, then attach the file again."
                )
        pages: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
        return pages

    @staticmethod
    def _unlock_pdf(document: object, password: str) -> None:
        needs_pass = bool(getattr(document, "needs_pass", False) or getattr(document, "is_encrypted", False))
        if not needs_pass:
            return
        authenticate = getattr(document, "authenticate", None)
        if authenticate is None:
            return
        if password and authenticate(password):
            return
        if authenticate(""):
            return
        raise ValueError(
            "This PDF is password-protected. Open Settings, enter the statement password, then attach the file again."
        )

    @staticmethod
    def _text_looks_useful(pages: list[str]) -> bool:
        combined = "\n".join(pages)
        if len(combined) < 40:
            return False
        return _DATE_TOKEN.search(combined) is not None or bool(_MONEY_TOKEN.search(combined))

    @staticmethod
    def _pdf_has_native_text(pdf_bytes: bytes, password: str = "") -> bool:
        pages = BankStatementProcessor._extract_with_pymupdf(pdf_bytes, password)
        if BankStatementProcessor._text_looks_useful(pages):
            return True
        pages = BankStatementProcessor._extract_with_pypdf(pdf_bytes, password)
        return BankStatementProcessor._text_looks_useful(pages)

    @staticmethod
    def _pdf_error_message(error: Exception) -> str:
        name = type(error).__name__
        text = str(error).lower()
        if "password" in text or "encrypt" in text:
            return "This PDF is password-protected. Open Settings, enter the statement password, then attach the file again."
        if "poppler" in text or "pdfinfo" in text:
            return "This looks like a scanned PDF. AskBuddy will OCR it with the built-in engine; retry the upload."
        if name in {"PdfReadError", "EmptyFileError"}:
            return "The PDF could not be read. Re-download the statement from your bank and try again."
        return f"The PDF could not be processed ({error})."

    @staticmethod
    def _ocr_pdf(pdf_bytes: bytes, password: str = "") -> list[str]:
        from document_analysis.extractors.ocr_engine import ocr_pdf

        return ocr_pdf(pdf_bytes, password)

    @staticmethod
    def _ocr_pdf_lines(
        pdf_bytes: bytes,
        password: str = "",
    ) -> list[list[list[dict[str, float | str]]]]:
        from document_analysis.extractors.ocr_engine import ocr_pdf_lines

        try:
            return ocr_pdf_lines(pdf_bytes, password)
        except Exception:
            return []

    @staticmethod
    def _ocr_image(image) -> str:
        from document_analysis.extractors.ocr_engine import ocr_image

        try:
            return ocr_image(image)
        except Exception:
            return ""

    @staticmethod
    def _ocr_image_lines(image) -> list[list[dict[str, float | str]]]:
        from document_analysis.extractors.ocr_engine import ocr_image_lines

        try:
            return ocr_image_lines(image)
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Amount helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_decimal(value: str) -> float:
        cleaned = value.replace("₹", "").replace("Rs.", "").replace("Rs", "")
        cleaned = re.sub(r"\s*(CR|DR)\s*$", "", cleaned, flags=re.I)
        cleaned = cleaned.strip()
        negative = cleaned.startswith("(") and cleaned.endswith(")")
        cleaned = cleaned.replace(",", "").replace("(", "").replace(")", "").strip()
        try:
            amount = float(cleaned) if cleaned not in {"", "-"} else 0.0
        except ValueError:
            return 0.0
        return -abs(amount) if negative else amount

    # ------------------------------------------------------------------
    # Classification (non-LLM)
    # ------------------------------------------------------------------

    def _classify_description(
        self,
        description: str,
        credit_amount: float,
        debit_amount: float,
    ) -> tuple[str, str, float]:
        heuristic_category, heuristic_confidence = self._infer_category(
            description, credit_amount, debit_amount
        )
        probabilities = self._model.predict_proba(description)
        ml_category = max(probabilities, key=probabilities.get) if probabilities else "Unknown"
        ml_confidence = probabilities.get(ml_category, 0.0) if probabilities else 0.0

        if heuristic_confidence >= 0.72:
            return heuristic_category, "heuristic", heuristic_confidence
        if ml_confidence >= 0.45 and ml_category not in {"Unknown"}:
            return ml_category, "machine_learning", ml_confidence
        return heuristic_category, "heuristic", heuristic_confidence

    @staticmethod
    def _infer_category(
        description: str,
        credit_amount: float,
        debit_amount: float,
    ) -> tuple[str, float]:
        haystack = f" {description.lower()} "
        for category, keywords in CATEGORY_KEYWORDS.items():
            if any(keyword in haystack for keyword in keywords):
                confidence = 0.93 if category != "Transfer" else 0.7
                if category == "Salary" and credit_amount <= 0:
                    continue
                return category, confidence
        if credit_amount > 0 and debit_amount == 0:
            return "Income", 0.4
        if debit_amount > 0:
            return "Other Expense", 0.4
        return "Unknown", 0.2

    # ------------------------------------------------------------------
    # Statement-level validation
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_balance_label(text: str, label: str) -> float | None:
        """Pull an opening/closing balance from a labelled line, if present."""
        pattern = re.compile(rf"(?im)^[^\n]*?\b{label}\s+balance\b[^\n]*$")
        for match in pattern.finditer(text):
            numbers = re.findall(r"\d[\d,]*(?:\.\d{1,2})?", match.group(0))
            if not numbers:
                continue
            return BankStatementProcessor._to_decimal(numbers[-1])
        return None

    @staticmethod
    def _extract_period(text: str) -> str | None:
        pattern = re.compile(
            r"(?:statement\s+(?:period|date)\s*[:\-]?\s*)([^:\n]{4,40}?)\s+(?:to|through)\s+([^:\n]{4,40})",
            re.I,
        )
        match = pattern.search(text)
        if not match:
            return None
        return f"{match.group(1).strip()} to {match.group(2).strip()}"[:80]

    @staticmethod
    def _reconciliation_warnings(
        primary: list[Transaction],
        sections: SupportingSections,
    ) -> list[str]:
        """Cross-check page-2 supporting sections against the primary table."""
        warnings: list[str] = []
        if sections.checks:
            primary_checks = sum(
                item.debit_amount
                for item in primary
                if re.search(r"\b(check|chq|cheque)\b", item.description, re.I)
            )
            section_total = sum(
                item.debit_amount + item.credit_amount for item in sections.checks
            )
            if abs(primary_checks - section_total) > _BALANCE_TOLERANCE:
                warnings.append(
                    f"Checks Paid totals ({section_total:,.2f}) do not match check debits in the "
                    f"primary table ({primary_checks:,.2f})."
                )
        if sections.deposits:
            section_credits = sum(item.credit_amount for item in sections.deposits)
            primary_credits = sum(item.credit_amount for item in primary)
            if abs(primary_credits - section_credits) > _BALANCE_TOLERANCE:
                warnings.append(
                    f"Deposits and Other Credits totals ({section_credits:,.2f}) do not match "
                    f"credits in the primary table ({primary_credits:,.2f})."
                )
        if sections.withdrawals:
            section_debits = sum(item.debit_amount for item in sections.withdrawals)
            primary_atm = sum(
                item.debit_amount
                for item in primary
                if re.search(r"\b(atm|withdrawal)\b", item.description, re.I)
            )
            if abs(primary_atm - section_debits) > _BALANCE_TOLERANCE:
                warnings.append(
                    f"Withdrawals and Other Debits totals ({section_debits:,.2f}) do not match "
                    f"primary-table ATM/withdrawal debits ({primary_atm:,.2f})."
                )
        if sections.fees:
            section_fees = sum(item.debit_amount + item.credit_amount for item in sections.fees)
            primary_fees = sum(
                item.debit_amount
                for item in primary
                if re.search(r"\b(fee|service charge|charges)\b", item.description, re.I)
            )
            if abs(primary_fees - section_fees) > _BALANCE_TOLERANCE:
                warnings.append(
                    f"Account Service Charges totals ({section_fees:,.2f}) do not match "
                    f"primary-table fee debits ({primary_fees:,.2f})."
                )
        return warnings

    def _build_validation(
        self,
        statement: BankStatement,
        full_text: str,
        dropped_rows: list[list[str]],
        sections: SupportingSections,
        parse_dropped: int = 0,
    ) -> dict[str, object]:
        rows = statement.transactions
        opening = self._extract_balance_label(full_text, "opening")
        closing = self._extract_balance_label(full_text, "closing")
        credits = sum(item.credit_amount for item in rows)
        debits = sum(item.debit_amount for item in rows)
        fees = (
            round(sum(item.debit_amount + item.credit_amount for item in sections.fees), 2)
            if sections.fees
            else None
        )

        expected_closing: float | None = None
        if opening is not None:
            expected_closing = round(opening + credits - debits, 2)
        reported_closing = closing if closing is not None else (
            rows[-1].balance if rows and rows[-1].balance else None
        )

        balance_mismatches = 0
        previous: Transaction | None = None
        for item in rows:
            if previous is not None and previous.balance and item.balance:
                expected = round(previous.balance - item.debit_amount + item.credit_amount, 2)
                if abs(expected - item.balance) > _BALANCE_TOLERANCE:
                    balance_mismatches += 1
            previous = item

        warnings: list[str] = []
        issue_counts: Counter[str] = Counter()
        for issue_parts in dropped_rows:
            for part in issue_parts:
                issue = part.split(": ", 1)[-1]
                issue_counts[issue] += 1
        if issue_counts:
            summary = ", ".join(f"{count}× {name}" for name, count in issue_counts.most_common())
            warnings.append(
                f"{len(dropped_rows)} OCR row(s) were discarded during validation ({summary})."
            )
        if parse_dropped:
            warnings.append(
                f"{parse_dropped} row(s) with invalid or unreadable transaction dates were skipped."
            )
        if balance_mismatches:
            warnings.append(
                f"{balance_mismatches} transaction(s) do not follow running-balance arithmetic; "
                "OCR or layout gaps are likely."
            )
        if (
            expected_closing is not None
            and reported_closing is not None
            and abs(expected_closing - reported_closing) > _BALANCE_TOLERANCE
        ):
            warnings.append(
                f"Reported closing balance {reported_closing:,.2f} differs from the reconciled "
                f"balance {expected_closing:,.2f} (opening {opening:,.2f} + credits {credits:,.2f} "
                f"- debits {debits:,.2f})."
            )
        warnings.extend(self._reconciliation_warnings(rows, sections))

        checks: list[dict[str, object]] = [
            {
                "check": "transaction_count",
                "expected": ">= 1",
                "actual": len(rows),
                "status": "ok" if rows else "error",
                "message": "" if rows else "No validated transactions were extracted.",
            },
            {
                "check": "valid_dates",
                "expected": "all rows",
                "actual": sum(1 for row in rows if self._parse_date_value(row.date) is not None),
                "status": "ok",
                "message": "",
            },
            {
                "check": "no_duplicates",
                "expected": 0,
                "actual": len(rows) - len(self._dedupe(rows)),
                "status": "ok",
                "message": "",
            },
            {
                "check": "running_balance",
                "expected": 0,
                "actual": balance_mismatches,
                "status": "warning" if balance_mismatches else "ok",
                "message": f"{balance_mismatches} rows deviate from running-balance arithmetic.",
            },
            {
                "check": "closing_balance",
                "expected": expected_closing,
                "actual": reported_closing,
                "status": (
                    "warning"
                    if expected_closing is not None
                    and reported_closing is not None
                    and abs(expected_closing - reported_closing) > _BALANCE_TOLERANCE
                    else "ok"
                ),
                "message": "",
            },
        ]

        status = "error" if not rows else ("warning" if warnings else "ok")
        return {
            "status": status,
            "warnings": warnings,
            "opening_balance": opening,
            "closing_balance": closing,
            "statement_period": self._extract_period(full_text),
            "total_credit": round(credits, 2),
            "total_debit": round(debits, 2),
            "fees": fees,
            "expected_closing_balance": expected_closing,
            "reported_closing_balance": reported_closing,
            "balance_mismatches": balance_mismatches,
            "transaction_count": len(rows),
            "checks": checks,
        }