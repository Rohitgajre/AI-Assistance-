"""Regression tests for the LLM-free bank-statement pipeline.

Each scenario mirrors a corruption or edge case observed in the original
pipeline (OCR junk treated as dates/amounts, page-2 detail sections merged into
the primary table, invalid rows exported, most-rows-wins parser selection) and
verifies the fixed behaviour end to end.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from conftest import (
    ACCOUNT_NUMBER,
    CHECK_SUPPORTING,
    DEPOSIT_SUPPORTING,
    SAMPLE_TRANSACTIONS,
    TOTAL_CREDIT,
    TOTAL_DEBIT,
)
from openpyxl import load_workbook

from bank_statement_processor import EXPORT_FIELDS, BankStatementProcessor, Transaction

EXPECTED_DATES = {row[0] for row in SAMPLE_TRANSACTIONS}
PAGE_BREAK = "\f"


def _parse_csv(raw: bytes) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    return [dict(row) for row in reader]


def _parse_xlsx(raw: bytes) -> list[dict[str, object]]:
    workbook = load_workbook(io.BytesIO(raw))
    sheet = workbook["Transactions"]
    header = [str(cell.value) for cell in sheet[1]]
    rows: list[dict[str, object]] = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if all(value is None for value in row):
            continue
        rows.append(dict(zip(header, row, strict=False)))
    return rows


def _assert_same_dataset(csv_rows: list[dict[str, str]], xlsx_rows: list[dict[str, object]]) -> None:
    numeric_fields = {"debit_amount", "credit_amount", "balance", "confidence"}
    assert len(csv_rows) == len(xlsx_rows)
    for csv_row, xlsx_row in zip(csv_rows, xlsx_rows, strict=True):
        for field in EXPORT_FIELDS:
            if field in numeric_fields:
                assert float(csv_row[field]) == pytest.approx(float(xlsx_row[field])), field
            else:
                assert csv_row[field] == str(xlsx_row[field]), field


def test_two_page_section_isolation() -> None:
    """Page-2 detail sections must never merge into the primary transaction table."""
    page1 = (
        "HDFC BANK\n"
        f"Account No: {ACCOUNT_NUMBER}\n"
        "Date Description Debit Credit Balance\n"
        + "\n".join(f"{date} {desc} {debit} {credit} {balance}" for date, desc, debit, credit, balance in SAMPLE_TRANSACTIONS[:4])
    )
    page2 = "\n".join(
        [
            "Deposits and Other Credits",
            "10/03 PREAUTHORIZED CREDIT 2500.00",
            "Withdrawals and Other Debits",
            "10/22 ATM WITHDRAWAL 3000.00",
            "Account Service Charges and Fees",
            "11/09 SERVICE CHARGE 150.00",
            "Checks Paid",
            "10/05 CHECK 1234 750.00",
            "10/12 CHECK 1236 1000.00",
        ]
    )
    statement = BankStatementProcessor().process_text(
        page1 + PAGE_BREAK + page2, "two_page.txt"
    )

    # Only the four page-1 rows survive as transactions; none are check rows.
    assert len(statement.transactions) == 4
    descriptions = [item.description for item in statement.transactions]
    assert descriptions == ["POS PURCHASE", "PREAUTHORIZED CREDIT", "POS PURCHASE", "CHECK 1234"]
    assert all("CHECK 1236" not in item.description for item in statement.transactions)

    # The page-2 sections are parsed separately for cross-checking.
    assert [
        (item.date, item.description, item.debit_amount) for item in statement.supporting_sections.checks
    ] == [("10/05", "CHECK 1234", 750.0), ("10/12", "CHECK 1236", 1000.0)]
    assert [
        (item.date, item.description, item.credit_amount) for item in statement.supporting_sections.deposits
    ] == [("10/03", "PREAUTHORIZED CREDIT", 2500.0)]
    assert [
        (item.date, item.description, item.debit_amount) for item in statement.supporting_sections.withdrawals
    ] == [("10/22", "ATM WITHDRAWAL", 3000.0)]
    assert [
        (item.date, item.description, item.debit_amount) for item in statement.supporting_sections.fees
    ] == [("11/09", "SERVICE CHARGE", 150.0)]


def test_scanned_two_page_acceptance_real_ocr(scanned_sample_pdf: bytes) -> None:
    """The 2-page scanned sample processed with the real OCR engine must be clean."""
    processor = BankStatementProcessor()
    statement = processor.process_pdf(scanned_sample_pdf, "sample_scan.pdf")

    assert statement.document_type == "image"
    assert statement.account_number == ACCOUNT_NUMBER
    assert statement.opening_balance == 10000.0
    assert statement.closing_balance == 4300.0
    assert statement.as_summary()["total_debit"] == TOTAL_DEBIT
    assert statement.as_summary()["total_credit"] == TOTAL_CREDIT

    rows = statement.transactions
    assert len(rows) == len(SAMPLE_TRANSACTIONS)

    # Exact dataset: dates, descriptions, amounts, balances match the source rows.
    assert sorted((item.date, item.description) for item in rows) == sorted(
        (date, desc) for date, desc, _d, _c, _b in SAMPLE_TRANSACTIONS
    )
    assert sorted((item.date, item.debit_amount, item.credit_amount, item.balance) for item in rows) == sorted(
        (date, float(debit), float(credit), float(balance)) for date, _d, debit, credit, balance in SAMPLE_TRANSACTIONS
    )

    # No corrupted dates or fabricated amounts survive.
    assert {item.date for item in rows} == EXPECTED_DATES
    assert all("00-00-00" not in item.date for item in rows)
    assert all(item.debit_amount <= 1_000_000 and item.credit_amount <= 1_000_000 for item in rows)
    assert all(item.debit_amount >= 0 and item.credit_amount >= 0 for item in rows)

    # Classification stays non-LLM.
    assert all(item.classification_method in {"heuristic", "machine_learning"} for item in rows)

    # The page-2 supporting sections round-trip to the same figures.
    assert [
        (item.date, item.description, item.debit_amount) for item in statement.supporting_sections.checks
    ] == [(date, desc, float(amount)) for date, desc, amount in CHECK_SUPPORTING]
    assert [
        (item.date, item.description, item.credit_amount) for item in statement.supporting_sections.deposits
    ] == [(date, desc, float(amount)) for date, desc, amount in DEPOSIT_SUPPORTING]

    # CSV and XLSX exports contain identical clean datasets.
    csv_bytes = processor.export_bytes(rows, "csv", statement)
    xlsx_bytes = processor.export_bytes(rows, "xlsx", statement)
    csv_rows = _parse_csv(csv_bytes)
    assert len(csv_rows) == len(SAMPLE_TRANSACTIONS)
    assert all("00-00-00" not in row["date"] for row in csv_rows)
    assert all("12 CHECK" not in row["date"] and "CHECK 1236" != row["description"].split()[0] for row in csv_rows)
    _assert_same_dataset(csv_rows, _parse_xlsx(xlsx_bytes))


def test_wrapped_description_is_joined() -> None:
    text = """
    ICICI Bank
    Customer Name: Rohan Mehta
    A/C No: 998877665544
    Date Particulars Amount Balance
    19-Sep-2024 UPI/SWIGGY/FOOD 450.00 DR 12000.00
    late night order
    20-Sep-2024 NEFT SALARY ACME CORP 75000.00 CR 87000.00
    """
    statement = BankStatementProcessor().process_text(text, "wrapped.txt")
    assert len(statement.transactions) == 2
    assert statement.transactions[0].description == "UPI/SWIGGY/FOOD late night order"
    assert statement.transactions[0].debit_amount == 450.0
    assert statement.transactions[1].credit_amount == 75000.0


def test_missing_balance_column_line_based() -> None:
    text = """
    HDFC BANK
    Account No: 12345678
    Date Description Debit Credit
    10/02 POS PURCHASE 500.00 0.00
    10/03 PREAUTHORIZED CREDIT 0.00 2500.00
    """
    statement = BankStatementProcessor().process_text(text, "no_balance.txt")
    assert len(statement.transactions) == 2
    assert statement.transactions[0].debit_amount == 500.0
    assert statement.transactions[0].credit_amount == 0.0
    assert statement.transactions[1].credit_amount == 2500.0
    # A missing balance column must not fabricate balances or raise mismatches.
    assert all(item.balance == 0.0 for item in statement.transactions)
    assert statement.validation is not None and statement.validation["status"] == "ok"


def test_parenthesized_negative_amounts() -> None:
    text = """
    HDFC BANK
    Account No: 12345678
    Date Description Amount Balance
    10/02 POS PURCHASE (500.00) 9500.00
    10/03 PREAUTHORIZED CREDIT 2500.00 12000.00
    """
    statement = BankStatementProcessor().process_text(text, "parens.txt")
    assert len(statement.transactions) == 2
    assert statement.transactions[0].debit_amount == 500.0
    assert statement.transactions[0].credit_amount == 0.0
    assert statement.transactions[0].balance == 9500.0
    assert statement.transactions[1].credit_amount == 2500.0


def test_multiple_date_formats_normalized() -> None:
    text = """
    HDFC BANK
    Account No: 12345678
    Date Description Debit Credit Balance
    03/09/2024 SALARY CREDIT 0.00 50000.00 250000.00
    05-09-2024 AMAZON PAY 2999.00 0.00 247001.00
    19-Sep-2024 UPI SWIGGY 450.00 DR 12000.00
    2024-09-20 NEFT SALARY 75000.00 CR 87000.00
    """
    statement = BankStatementProcessor().process_text(text, "formats.txt")
    assert [item.date for item in statement.transactions] == [
        "2024-09-03",
        "2024-09-05",
        "2024-09-19",
        "2024-09-20",
    ]
    assert all(item.date != "00-00-00" for item in statement.transactions)


def test_duplicate_rows_deduped() -> None:
    text = """
    HDFC BANK
    Account No: 12345678
    Date Description Debit Credit Balance
    12/09/2024 RENT PAYMENT 22000.00 0.00 223501.00
    12/09/2024 RENT PAYMENT 22000.00 0.00 223501.00
    12/09/2024 RENT PAYMENT 22000.00 0.00 223501.00
    """
    statement = BankStatementProcessor().process_text(text, "dupes.txt")
    assert len(statement.transactions) == 1
    assert statement.transactions[0].description == "RENT PAYMENT"
    assert statement.validation is not None and statement.validation["checks"][2]["actual"] == 0


def test_ocr_malformed_date_tokens_are_not_dates() -> None:
    """'12 CHECK 1236' must not become a date, and '00-00-00' rows are dropped."""
    text = """
    HDFC BANK
    Account No: 12345678
    Date Description Debit Credit Balance
    12 CHECK 1236 1000.00 0.00 6500.00
    10/12 CHECK 1236 1000.00 0.00 6500.00
    00-00-00 POS PURCHASE 500.00 0.00 6000.00
    """
    statement = BankStatementProcessor().process_text(text, "bad_dates.txt")
    assert len(statement.transactions) == 1
    assert statement.transactions[0].date == "10/12"
    assert statement.transactions[0].description == "CHECK 1236"
    assert statement.transactions[0].debit_amount == 1000.0
    assert any("skipped" in warning for warning in statement.warnings), statement.warnings

    csv_rows = _parse_csv(BankStatementProcessor().export_bytes(statement.transactions, "csv", statement))
    assert all("00-00-00" not in row["date"] and "12 CHECK" not in row["date"] for row in csv_rows)


def test_terminal_id_never_becomes_an_amount() -> None:
    text = """
    HDFC BANK
    Account No: 12345678
    Date Description Debit Credit Balance
    10/22 ATM WITHDRAWAL 24349201 3000.00 0.00 4450.00
    """
    statement = BankStatementProcessor().process_text(text, "terminal.txt")
    assert len(statement.transactions) == 1
    transaction = statement.transactions[0]
    assert transaction.debit_amount == 3000.0
    assert transaction.credit_amount == 0.0
    assert transaction.balance == 4450.0
    # The terminal id survives only as text, never as a numeric amount.
    assert "24349201" in transaction.description
    assert transaction.debit_amount != 24349201.0 and transaction.credit_amount != 24349201.0


def test_account_number_extraction_edge_cases() -> None:
    processor = BankStatementProcessor()
    # OCR fragment with no digits is rejected instead of being used as the account.
    assert processor._extract_account_number("Account No: TIVITY") is None
    assert processor._extract_account_number("Account No: TIVITY XXXX") is None
    # Trailing mask placeholders are stripped.
    assert processor._extract_account_number("Account No: 12345678XXXX") == "12345678"
    assert processor._extract_account_number("Account #12345678") == "12345678"

    statement = processor.process_text(
        "HDFC BANK\nAccount #12345678\nDate Description Debit Credit Balance\n"
        "10/02 POS PURCHASE 500.00 0.00 9500.00\n",
        "acct.txt",
    )
    assert statement.account_number == "12345678"


def test_account_number_extraction_skips_activity_false_match() -> None:
    """A bare ``ac`` prefix must not match inside words such as "Activity"."""
    processor = BankStatementProcessor()
    text = (
        "Activity for Relationship Checking - Account #12345678\n"
        "Date Description Debit Credit Balance\n"
        "10/02 POS PURCHASE 500.00 0.00 9500.00\n"
    )
    statement = processor.process_text(text, "account_sample.txt")
    assert statement.account_number == "12345678"


def test_balance_mismatch_warnings() -> None:
    text = """
    HDFC BANK
    Account No: 12345678
    Date Description Debit Credit Balance
    10/02 POS PURCHASE 500.00 0.00 9500.00
    10/03 POS PURCHASE 300.00 0.00 9000.00
    Opening Balance: 10000.00
    Closing Balance: 9999.00
    """
    statement = BankStatementProcessor().process_text(text, "mismatch.txt")
    joined = "\n".join(statement.warnings)
    assert "do not follow running-balance arithmetic" in joined
    assert "Reported closing balance" in joined
    assert statement.validation is not None and statement.validation["status"] == "warning"
    running_check = next(check for check in statement.validation["checks"] if check["check"] == "running_balance")
    assert running_check["status"] == "warning"


def test_supporting_sections_reconciliation_warning() -> None:
    """Missing check rows in the primary table surface a cross-check warning."""
    page1 = (
        "HDFC BANK\n"
        f"Account No: {ACCOUNT_NUMBER}\n"
        "Date Description Debit Credit Balance\n"
        "10/05 CHECK 1234 750.00 0.00 10050.00\n"
        "10/08 POS PURCHASE 2250.00 0.00 7800.00\n"
    )
    page2 = "Checks Paid\n10/05 CHECK 1234 750.00\n10/12 CHECK 1236 1000.00\n"
    statement = BankStatementProcessor().process_text(page1 + PAGE_BREAK + page2, "recon.txt")
    assert len(statement.transactions) == 2
    assert any("Checks Paid totals" in warning for warning in statement.warnings), statement.warnings


# ----------------------------------------------------------------------
# US-layout statements (the shape of the scanned sample PDF)
# ----------------------------------------------------------------------
# The figures below are arithmetically self-consistent: the balance column
# chains from the 69.96 opening balance to the 575.57 closing balance, and
# every page-2 detail total equals the matching page-1 column sum. The only
# deliberate defect is the interest credit, printed as "26" the way the scan
# of dummy_statement.pdf loses the leading "0." - the detail section's "0.26"
# is what the running balance proves.
US_PAGE1 = """\
FIRST NATIONAL BANK
Account Number: 12345678
JAMES C. MORRISON
1765 SHERIDAN DRIVE
YOUR CITY, USA 03087
Statement Period: October 10 to November 9
Summary of Your Account
Average Balance 5043.24
Beginning balance on October 10 $69.96 Avg Collected Balance $643.24
Ending balance on November 9 $345.10 Average Balance for APY $643.24
Date Description Debit Credit Balance
10/02 POS PURCHASE 4.23 0.00 65.73
10/03 PREAUTHORIZED CREDIT 0.00 763.01 828.74
10/05 CHECK 1234 9.98 0.00 818.76
10/05 POS PURCHASE 25.50 0.00 793.26
10/12 CHECK 1236 69.00 0.00 724.26
10/14 CHECK 1237 180.63 0.00 543.63
10/16 PREAUTHORIZED CREDIT 0.00 763.01 1306.64
10/22 ATM WITHDRAWAL 140.00 0.00 1166.64
10/28 CHECK 1238 91.06 0.00 1075.58
10/30 CHECK 1239 451.20 0.00 624.38
10/30 CHECK 1246 37.07 0.00 587.31
10/31 CHECK 1247 100.00 0.00 487.31
10/31 CHECK 1248 78.24 0.00 409.07
11/02 CHECK 1249 52.23 0.00 356.84
11/09 INTERESTCREDIT 0.00 26.00 357.10
11/09 SERVICE CHARGE 12.00 0.00 345.10
"""

US_PAGE2 = """\
Deposits and Other Credits
Date Description Amount
10/03 PREAUTHORIZED CREDIT PAYROLL 0987654 678990 763.01
10/16 PREAUTHORIZED CREDIT US TREASURY 310 SOC SEC 02080 763.01
11/09 INTEREST CREDIT 0.26
Withdrawals and Other Debits
Date Description Amount
10/02 POS PURCHASE TERMINAL 24349201 WAL-MART#3492 4.23
10/05 POS PURCHASE TERMINAL 422443 KWAN COURT WICHITA KS 25.50
10/22 ATM WITHDRAWAL CASH WITHDRAWAL TERMINAL S78476 3216 140.00
Account Service Charges and Fees
Date Description Amount
11/09 SERVICE CHARGE 12.00
Checks Paid (*indicates out of sequence)
Check # Date Amount Check # Date Amount Check # Date Amount
1234 10/05 $9.98 1238 10/28 $91.06 1247 10/31 $100.00
1236* 10/12 $69.00 1239 10/30 $451.20 1248 10/31 $78.24
1237 10/14 $180.63 1246* 10/30 $37.07 1249 11/02 $52.23
"""


def test_us_layout_holder_and_dated_summary_balances() -> None:
    """Label-less name block and "Balance on <date>" summary are both read."""
    statement = BankStatementProcessor().process_text(US_PAGE1 + PAGE_BREAK + US_PAGE2, "us.txt")

    assert statement.account_holder_name == "JAMES C. MORRISON"
    assert statement.account_number == "12345678"
    assert statement.opening_balance == 69.96
    assert statement.closing_balance == 345.10
    assert statement.statement_period == "October 10 to November 9"
    assert statement.validation["expected_closing_balance"] == 345.10


def test_us_layout_period_falls_back_to_dated_summary() -> None:
    """With no "Statement Period" line the summary dates supply the period."""
    without_period = "\n".join(
        line for line in US_PAGE1.splitlines() if not line.startswith("Statement Period")
    )
    statement = BankStatementProcessor().process_text(without_period + PAGE_BREAK + US_PAGE2, "us2.txt")
    assert statement.statement_period == "October 10 to November 9"


def test_us_layout_detail_sections_never_enter_the_primary_table() -> None:
    """Page-2 detail rows, including their column headers, stay out of the rows."""
    statement = BankStatementProcessor().process_text(US_PAGE1 + PAGE_BREAK + US_PAGE2, "us3.txt")

    assert [item.description for item in statement.transactions] == [
        "POS PURCHASE",
        "PREAUTHORIZED CREDIT",
        "CHECK 1234",
        "POS PURCHASE",
        "CHECK 1236",
        "CHECK 1237",
        "PREAUTHORIZED CREDIT",
        "ATM WITHDRAWAL",
        "CHECK 1238",
        "CHECK 1239",
        "CHECK 1246",
        "CHECK 1247",
        "CHECK 1248",
        "CHECK 1249",
        "INTERESTCREDIT",
        "SERVICE CHARGE",
    ]
    # A bare "Date Description Amount" header inside a detail section must not
    # reopen the primary table, so no header, terminal id or check number
    # survives as a description, and no "Transaction" row is invented.
    assert not any("Description" in item.description for item in statement.transactions)
    assert not any("Transaction" in item.description for item in statement.transactions)
    # Ungrouped 4-digit amounts keep their decimal point (1306.64, not 130.0).
    assert [item.balance for item in statement.transactions] == [
        65.73, 828.74, 818.76, 793.26, 724.26, 543.63, 1306.64, 1166.64,
        1075.58, 624.38, 587.31, 487.31, 409.07, 356.84, 357.10, 345.10,  # fmt: skip
    ]


def test_us_layout_checks_triplet_ledger_is_read_positionally() -> None:
    """"Checks Paid" printed as Check#/Date/Amount triplets parses per check."""
    statement = BankStatementProcessor().process_text(US_PAGE1 + PAGE_BREAK + US_PAGE2, "us4.txt")
    assert [
        (item.date, item.description, item.debit_amount)
        for item in statement.supporting_sections.checks
    ] == [
        ("10/05", "CHECK 1234", 9.98),
        ("10/28", "CHECK 1238", 91.06),
        ("10/31", "CHECK 1247", 100.00),
        ("10/12", "CHECK 1236", 69.00),
        ("10/30", "CHECK 1239", 451.20),
        ("10/31", "CHECK 1248", 78.24),
        ("10/14", "CHECK 1237", 180.63),
        ("10/30", "CHECK 1246", 37.07),
        ("11/02", "CHECK 1249", 52.23),
    ]
    # The out-of-sequence marker belongs to the check number, not the amount.
    assert sum(item.debit_amount for item in statement.supporting_sections.checks) == pytest.approx(1069.41)
    # All nine checks are in the primary table, so the cross-check stays silent.
    assert not any("Checks Paid totals" in warning for warning in statement.warnings), statement.warnings


def test_us_layout_detail_amount_repairs_misread_cell_and_reports_it() -> None:
    """A lost glyph is repaired from the statement itself, never silently."""
    statement = BankStatementProcessor().process_text(US_PAGE1 + PAGE_BREAK + US_PAGE2, "us5.txt")
    interest = next(item for item in statement.transactions if item.date == "11/09" and item.credit_amount)

    # "0.26" was read as "26"; the detail section supplies the real figure.
    assert interest.credit_amount == 0.26
    assert interest.balance == 357.10
    repair = next(warning for warning in statement.warnings if "detail section" in warning)
    assert "0.26" in repair and "26.00" in repair
    # Every row now follows the running balance and the totals still reconcile.
    assert statement.validation["balance_mismatches"] == 0
    assert statement.validation["expected_closing_balance"] == 345.10
    assert statement.as_summary()["total_credit"] == pytest.approx(763.01 + 763.01 + 0.26)
    assert statement.as_summary()["total_debit"] == pytest.approx(1251.14)
    assert not any("do not match" in warning for warning in statement.warnings), statement.warnings


def test_balance_summary_line_after_the_table_is_not_a_description() -> None:
    """"Ending balance" printed under the table is a label, not a row tail."""
    text = (
        "HDFC BANK\n"
        f"Account No: {ACCOUNT_NUMBER}\n"
        "Date Description Debit Credit Balance\n"
        "10/02 POS PURCHASE 500.00 0.00 9500.00\n"
        "Opening Balance: 10000.00\n"
        "Ending Balance on November 9 $9500.00\n"
        "Average Collected Balance $9100.00\n"
    )
    statement = BankStatementProcessor().process_text(text, "tail.txt")
    assert [item.description for item in statement.transactions] == ["POS PURCHASE"]
    assert statement.closing_balance == 9500.0


def test_quality_score_beats_row_count() -> None:
    """A small validated candidate wins over a larger all-garbage candidate."""
    processor = BankStatementProcessor()
    text = (
        "HDFC BANK\n"
        f"Account No: {ACCOUNT_NUMBER}\n"
        "Date Description Debit Credit Balance\n"
        "10/02 POS 500.00 200.00 9500.00\n"
        "10/03 POS 300.00 100.00 9200.00\n"
        "10/04 POS 200.00 50.00 9000.00\n"
    )
    clean = [
        Transaction(
            date="10/05",
            description="CLEAN PAYMENT",
            debit_amount=100.0,
            credit_amount=0.0,
            balance=8900.0,
        )
    ]
    statement = processor._build_statement([text], "quality.txt", table_candidate=clean)
    assert len(statement.transactions) == 1
    assert statement.transactions[0].description == "CLEAN PAYMENT"
    assert statement.transactions[0].date == "10/05"
    assert statement.transactions[0].debit_amount == 100.0


def test_csv_export_clean_rows_only() -> None:
    text = """
    HDFC BANK
    Account No: 12345678
    Date Description Debit Credit Balance
    10/02 POS PURCHASE 500.00 0.00 9500.00
    00-00-00 POS PURCHASE 700.00 0.00 8800.00
    10/03 SPLIT BOTH SIDES 300.00 100.00 9000.00
    """
    statement = BankStatementProcessor().process_text(text, "clean.csv")
    assert len(statement.transactions) == 1
    assert statement.transactions[0].date == "10/02"

    csv_rows = _parse_csv(BankStatementProcessor().export_bytes(statement.transactions, "csv", statement))
    assert [row["description"] for row in csv_rows] == ["POS PURCHASE"]
    assert all("00-00-00" not in row["date"] for row in csv_rows)
    assert all(float(row["debit_amount"]) <= 1_000_000 for row in csv_rows)


def test_xlsx_summary_transactions_validation_sheets() -> None:
    text = """
    HDFC BANK
    Account No: 12345678
    Date Description Debit Credit Balance
    10/02 POS PURCHASE 500.00 0.00 9500.00
    10/03 PREAUTHORIZED CREDIT 0.00 2500.00 12000.00
    """
    processor = BankStatementProcessor()
    statement = processor.process_text(text, "sheets.txt")
    workbook = load_workbook(io.BytesIO(processor.export_bytes(statement.transactions, "xlsx", statement)))
    assert workbook.sheetnames == ["Summary", "Transactions", "Validation"]

    summary = {row[0]: row[1] for row in workbook["Summary"].iter_rows(values_only=True)}
    assert summary["transaction_count"] == 2
    assert summary["total_debit"] == 500.0
    assert summary["total_credit"] == 2500.0

    transactions = workbook["Transactions"]
    assert [cell.value for cell in transactions[1]] == list(EXPORT_FIELDS)

    validation = workbook["Validation"]
    assert [cell.value for cell in validation[1]] == ["check", "expected", "actual", "status", "message"]
    assert validation.max_row > 1


def test_csv_matches_xlsx_dataset() -> None:
    text = """
    HDFC BANK
    Account No: 12345678
    Date Description Debit Credit Balance
    10/02 POS PURCHASE 500.00 0.00 9500.00
    10/03 PREAUTHORIZED CREDIT 0.00 2500.00 12000.00
    10/05 CHECK 1234 750.00 0.00 11250.00
    """
    processor = BankStatementProcessor()
    statement = processor.process_text(text, "both.txt")
    csv_rows = _parse_csv(processor.export_bytes(statement.transactions, "csv", statement))
    xlsx_rows = _parse_xlsx(processor.export_bytes(statement.transactions, "xlsx", statement))
    assert len(csv_rows) == 3
    _assert_same_dataset(csv_rows, xlsx_rows)


def test_export_transactions_writes_files(tmp_path: Path) -> None:
    processor = BankStatementProcessor()
    statement = processor.process_text(
        "HDFC BANK\nAccount No: 12345678\n"
        "Date Description Debit Credit Balance\n"
        "10/02 POS PURCHASE 500.00 0.00 9500.00\n",
        "file.txt",
    )
    csv_path = processor.export_transactions(statement, tmp_path / "stmt.csv")
    xlsx_path = processor.export_transactions(statement, tmp_path / "stmt.xlsx")
    assert csv_path.read_bytes().startswith(b"date,description")
    assert xlsx_path.read_bytes().startswith(b"PK")
    assert len(_parse_csv(csv_path.read_bytes())) == 1