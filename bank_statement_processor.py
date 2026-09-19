"""Bank statement extraction, classification, and export.

The pipeline is LLM-free: PDFs are parsed with pypdf and optional OCR,
transactions are classified with heuristics plus a multinomial Naive Bayes
model, and results export to CSV or Excel.
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    from openpyxl import Workbook
except ImportError:  # pragma: no cover - optional dependency in testing env
    Workbook = None


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
_DATE_TOKEN = re.compile(
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}\.\d{1,2}\.\d{2,4}"
    r"|\d{1,2}[-/ ][A-Za-z]{3,9}[-/ ]\d{2,4})\b",
    re.IGNORECASE,
)
_MONEY_TOKEN = re.compile(
    r"(?:₹|inr|rs\.?\s*)?"
    r"\(?\d{1,3}(?:,\d{2}){1,}(?:,\d{3})?(?:\.\d{1,2})?"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
    r"|\d+\.\d{1,2}"
    r"|(?<![\d.])(?!19\d{2}|20\d{2})\d{3,}(?![\d.])"
    r"\)?(?:\s*(?:cr|dr))?",
    re.IGNORECASE,
)
_NOISE_LINE = re.compile(
    r"(opening\s+balance|closing\s+balance|brought\s+forward|carried\s+forward|"
    r"\btotal\b|page\s+\d+|statement\s+summary|this\s+is\s+a\s+computer|"
    r"b/f\b|c/f\b)",
    re.IGNORECASE,
)
_IFSC = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")
_ACCOUNT_NO = re.compile(
    r"(?:account\s*(?:no\.?|number|#)|a/?c(?:\s*no\.?)?|acct\.?\s*no\.?)\s*[:\-]?\s*([A-Z0-9Xx*]{6,22})",
    re.IGNORECASE,
)
_HOLDER = re.compile(
    r"(?:account\s*(?:holder)?\s*name|customer\s*name|name\s+of\s+account\s+holder|primary\s+holder)\s*[:\-]\s*([A-Za-z0-9 .,&/'()-]+)",
    re.IGNORECASE,
)
_BANK_NAME = re.compile(r"^.*\bBANK\b.*$", re.IGNORECASE | re.MULTILINE)

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
    "classification_method",
    "confidence",
)


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
            "classification_method": self.classification_method,
            "confidence": round(self.confidence, 3),
        }


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

    def as_summary(self) -> dict[str, str | int | float | None]:
        debits = sum(item.debit_amount for item in self.transactions)
        credits = sum(item.credit_amount for item in self.transactions)
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


_RAPID_OCR = None


def _rapidocr_engine():
    global _RAPID_OCR
    if _RAPID_OCR is None:
        from rapidocr import RapidOCR

        _RAPID_OCR = RapidOCR()
    return _RAPID_OCR


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

    def process_text(
        self,
        raw_text: str,
        file_name: str,
        document_type: str = "text",
    ) -> BankStatement:
        cleaned = self._clean_text(raw_text)
        if not cleaned:
            raise ValueError("The uploaded bank statement is empty.")

        statement = BankStatement(
            file_name=file_name,
            document_type=document_type,
            bank_name=self._extract_bank_name(cleaned),
            account_holder_name=self._extract_holder(cleaned),
            account_number=self._extract_account_number(cleaned),
            ifsc_code=self._extract_ifsc(cleaned),
            transactions=self._extract_transactions(cleaned),
        )
        if not statement.transactions:
            statement.warnings.append("No transaction rows could be parsed from this document.")
        statement.transactions = self.classify_transactions(statement.transactions)
        statement.warnings.extend(self._balance_warnings(statement.transactions))
        return statement

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

        statement = self.process_text(combined, file_name, document_type="image" if used_ocr else "text")
        candidates = [statement.transactions]
        table_transactions = self._extract_transactions_from_pdf_tables(pdf_bytes, password)
        if table_transactions:
            candidates.append(table_transactions)
        window_transactions = self._extract_by_date_windows(combined)
        if window_transactions:
            candidates.append(window_transactions)

        best = max(candidates, key=len)
        if len(best) > len(statement.transactions):
            statement.transactions = self.classify_transactions(self._dedupe(best))
            statement.warnings = [w for w in statement.warnings if "No transaction rows" not in w]
            if not statement.transactions:
                statement.warnings.append("No transaction rows could be parsed from this document.")

        if not used_ocr and len(statement.transactions) < 2:
            try:
                ocr_pages = self._ocr_pdf(pdf_bytes, password)
                ocr_statement = self.process_text("\n".join(ocr_pages), file_name, document_type="image")
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
        rows = list(transactions)
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
            output = io.BytesIO()
            workbook.save(output)
            return output.getvalue()
        raise ValueError("Unsupported export format. Use csv or xlsx.")

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
        match = _ACCOUNT_NO.search(text)
        if match:
            return match.group(1).upper()
        return None

    @staticmethod
    def _extract_ifsc(text: str) -> str | None:
        labeled = re.search(r"IFSC(?:\s*CODE)?\s*[:\-]?\s*([A-Z]{4}0[A-Z0-9]{6})", text, re.I)
        if labeled:
            return labeled.group(1).upper()
        match = _IFSC.search(text.upper())
        return match.group(1) if match else None

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
    def _split_description_and_amounts(remainder: str) -> tuple[str, list[float], list[str]]:
        tokens = remainder.split()
        amount_spans: list[tuple[int, int, str]] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if BankStatementProcessor._looks_like_amount(token):
                width = 1
                flag_token = token
                if index + 1 < len(tokens) and re.fullmatch(r"(CR|DR)", tokens[index + 1], flags=re.I):
                    width = 2
                    flag_token = f"{token} {tokens[index + 1]}"
                amount_spans.append((index, width, flag_token))
                index += width
                continue
            index += 1
        if not amount_spans:
            return remainder.strip(" -|:/"), [], []
        chosen = amount_spans[-3:] if len(amount_spans) >= 3 else amount_spans
        skip: set[int] = set()
        amounts: list[float] = []
        flags: list[str] = []
        for start, width, flag_token in chosen:
            skip.update(range(start, start + width))
            amounts.append(BankStatementProcessor._to_decimal(flag_token))
            flags.append(flag_token.upper())
        description = " ".join(token for idx, token in enumerate(tokens) if idx not in skip)
        return description.strip(" -|:/"), amounts, flags

    @staticmethod
    def _looks_like_amount(token: str) -> bool:
        cleaned = token.replace("₹", "").replace(",", "")
        cleaned = re.sub(r"(?i)(rs\.?|inr)", "", cleaned)
        cleaned = re.sub(r"(?i)(cr|dr)$", "", cleaned).strip("() ")
        if not cleaned or cleaned in {".", "-"}:
            return False
        return bool(re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned))

    @staticmethod
    def _split_amounts(
        description: str,
        amounts: list[float],
        flags: list[str],
    ) -> tuple[float, float, float]:
        upper = description.upper()
        if len(amounts) >= 3:
            debit, credit, balance = amounts[0], amounts[1], amounts[-1]
            return debit, credit, balance
        if len(amounts) == 2:
            amount, balance = amounts[0], amounts[1]
        else:
            amount, balance = amounts[0], 0.0
        flag = flags[0] if flags else ""
        if "CR" in flag or any(token in upper for token in ("CREDIT", "SALARY", "INTEREST", "REFUND")):
            return 0.0, amount, balance
        if "DR" in flag or any(token in upper for token in ("DEBIT", "WITHDRAWAL", "POS", "UPI", "PURCHASE")):
            return amount, 0.0, balance
        if amount < 0:
            return abs(amount), 0.0, balance
        return amount, 0.0, balance

    @staticmethod
    def _dedupe(transactions: list[Transaction]) -> list[Transaction]:
        seen: set[tuple[str, str, float, float, float]] = set()
        unique: list[Transaction] = []
        for item in transactions:
            key = (
                item.date,
                item.description.lower(),
                item.debit_amount,
                item.credit_amount,
                item.balance,
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    @staticmethod
    def _balance_warnings(transactions: list[Transaction]) -> list[str]:
        warnings: list[str] = []
        previous: Transaction | None = None
        mismatches = 0
        for item in transactions:
            if previous is not None and previous.balance and item.balance:
                expected = round(previous.balance - item.debit_amount + item.credit_amount, 2)
                if abs(expected - item.balance) > 1.0:
                    mismatches += 1
            previous = item
        if mismatches:
            warnings.append(
                f"{mismatches} transaction(s) do not follow running-balance arithmetic; OCR or layout gaps are likely."
            )
        return warnings

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
            debit = self._cell_amount(cells, mapping.get("debit"))
            credit = self._cell_amount(cells, mapping.get("credit"))
            amount = self._cell_amount(cells, mapping.get("amount"))
            balance = self._cell_amount(cells, mapping.get("balance"))
            if amount and debit == 0 and credit == 0:
                debit, credit, balance = self._split_amounts(description, [amount, balance or 0.0], [joined])
            if not _DATE_TOKEN.search(date_value or joined):
                return None
            date_token = _DATE_TOKEN.search(date_value or joined)
            if date_token is None:
                return None
            if debit == 0 and credit == 0:
                return None
            return Transaction(
                date=self._normalize_date(date_token.group(1)),
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
        images = BankStatementProcessor._rasterize_pdf(pdf_bytes, password)
        ocr_text_parts: list[str] = []
        for image in images:
            text = BankStatementProcessor._ocr_image(image)
            if text:
                ocr_text_parts.append(text)
        if not ocr_text_parts:
            raise ValueError("No readable text was found in the scanned PDF.")
        return ocr_text_parts

    @staticmethod
    def _rasterize_pdf(pdf_bytes: bytes, password: str = ""):
        try:
            import pymupdf
        except ImportError as error:
            raise ValueError("PDF rendering is unavailable. Install pymupdf to process scanned statements.") from error

        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        BankStatementProcessor._unlock_pdf(document, password)
        images = []
        zoom = pymupdf.Matrix(2, 2)
        for page in document:
            pixmap = page.get_pixmap(matrix=zoom, alpha=False)
            from PIL import Image

            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            images.append(image)
        if not images:
            raise ValueError("The PDF has no pages to read.")
        return images

    @staticmethod
    def _ocr_image(image) -> str:
        rapid_text = BankStatementProcessor._ocr_with_rapidocr(image)
        if rapid_text:
            return rapid_text
        try:
            import pytesseract

            return pytesseract.image_to_string(image).strip()
        except Exception:
            return ""

    @staticmethod
    def _ocr_text_from_boxes(result: object) -> str:
        boxes = getattr(result, "boxes", None)
        texts = getattr(result, "txts", None)
        if boxes is None or texts is None:
            return ""
        items: list[tuple[float, float, str]] = []
        for box, text in zip(boxes, texts, strict=False):
            token = str(text).strip()
            if not token:
                continue
            try:
                points = list(box)
                ys = [float(point[1]) for point in points]
                xs = [float(point[0]) for point in points]
            except Exception:
                continue
            items.append((sum(ys) / max(len(ys), 1), sum(xs) / max(len(xs), 1), token))
        if not items:
            return ""
        items.sort(key=lambda item: (round(item[0] / 12.0), item[1]))
        lines: list[str] = []
        current_key: int | None = None
        current: list[tuple[float, str]] = []
        for y, x, token in items:
            key = int(round(y / 12.0))
            if current_key is None or key == current_key:
                current.append((x, token))
                current_key = key if current_key is None else current_key
                continue
            current.sort()
            lines.append(" ".join(part for _x, part in current))
            current = [(x, token)]
            current_key = key
        if current:
            current.sort()
            lines.append(" ".join(part for _x, part in current))
        return "\n".join(lines).strip()

    @staticmethod
    def _ocr_with_rapidocr(image) -> str:
        try:
            import numpy as np

            engine = _rapidocr_engine()
        except Exception:
            return ""
        result = engine(np.array(image))
        boxed = BankStatementProcessor._ocr_text_from_boxes(result)
        if boxed:
            return boxed
        texts = getattr(result, "txts", None)
        if texts:
            return "\n".join(str(item).strip() for item in texts if str(item).strip())
        payload = result[0] if isinstance(result, tuple) else result
        if payload is None:
            return ""
        lines: list[str] = []
        for item in payload:
            if isinstance(item, str):
                lines.append(item)
                continue
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                text = item[1]
                if isinstance(text, str):
                    lines.append(text)
        return "\n".join(line.strip() for line in lines if str(line).strip())

    @staticmethod
    def _normalize_date(raw_date: str) -> str:
        compact = re.sub(r"\s+", " ", raw_date.strip())
        for pattern in _DATE_PATTERNS:
            try:
                return datetime.strptime(compact, pattern).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return compact

    @staticmethod
    def _to_decimal(value: str) -> float:
        cleaned = value.replace("₹", "").replace("Rs.", "").replace("Rs", "")
        cleaned = re.sub(r"\s*(CR|DR)\s*$", "", cleaned, flags=re.I)
        cleaned = cleaned.replace(",", "").replace("(", "-").replace(")", "").strip()
        try:
            return abs(float(cleaned)) if cleaned not in {"", "-"} else 0.0
        except ValueError:
            return 0.0

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
