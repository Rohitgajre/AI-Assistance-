"""Builds a faithful two-page *scanned* (image-only) regression PDF.

The fixture mirrors the sample statement used for acceptance testing:

- page 1: the primary transaction table ``Date | Description | Debit | Credit
  | Balance`` plus account metadata and opening/closing balance labels;
- page 2: the supporting detail sections (Deposits and Other Credits,
  Withdrawals and Other Debits, Account Service Charges and Fees, Checks Paid)
  that must never be merged into the primary transaction list.
"""

from __future__ import annotations

import pymupdf
import pytest

# date, description, debit, credit, balance
SAMPLE_TRANSACTIONS: list[tuple[str, str, str, str, str]] = [
    ("10/02", "POS PURCHASE", "500.00", "0.00", "9500.00"),
    ("10/03", "PREAUTHORIZED CREDIT", "0.00", "2500.00", "12000.00"),
    ("10/04", "POS PURCHASE", "1200.00", "0.00", "10800.00"),
    ("10/05", "CHECK 1234", "750.00", "0.00", "10050.00"),
    ("10/05", "POS PURCHASE", "300.00", "0.00", "9750.00"),
    ("10/08", "POS PURCHASE", "2250.00", "0.00", "7500.00"),
    ("10/12", "CHECK 1236", "1000.00", "0.00", "6500.00"),
    ("10/14", "CHECK 1237", "600.00", "0.00", "5900.00"),
    ("10/14", "POS PURCHASE", "450.00", "0.00", "5450.00"),
    ("10/16", "PREAUTHORIZED CREDIT", "0.00", "2000.00", "7450.00"),
    ("10/22", "ATM WITHDRAWAL", "3000.00", "0.00", "4450.00"),
    ("11/09", "SERVICE CHARGE", "150.00", "0.00", "4300.00"),
]

CHECK_SUPPORTING: list[tuple[str, str, str]] = [
    ("10/05", "CHECK 1234", "750.00"),
    ("10/12", "CHECK 1236", "1000.00"),
    ("10/14", "CHECK 1237", "600.00"),
]

DEPOSIT_SUPPORTING: list[tuple[str, str, str]] = [
    ("10/03", "PREAUTHORIZED CREDIT", "2500.00"),
    ("10/16", "PREAUTHORIZED CREDIT", "2000.00"),
]

ACCOUNT_NUMBER = "12345678"
TOTAL_DEBIT = 10200.0
TOTAL_CREDIT = 4500.0

# x positions of the five primary-table columns (595pt-wide A4 page)
_COLUMNS = {"date": 42, "desc": 150, "debit": 342, "credit": 432, "balance": 518}


def _draw_page1(page) -> None:
    y = 60
    for text in (
        "HDFC BANK",
        "Account Holder Name: Rohan Mehta",
        f"Account No: {ACCOUNT_NUMBER}",
        "IFSC: HDFC0001234",
        "Statement Period: 01 Oct 2024 to 12 Nov 2024",
    ):
        page.insert_text((50, y), text, fontsize=11)
        y += 16
    y += 12
    page.insert_text((_COLUMNS["date"], y), "Date", fontsize=11)
    page.insert_text((_COLUMNS["desc"], y), "Description", fontsize=11)
    page.insert_text((_COLUMNS["debit"], y), "Debit", fontsize=11)
    page.insert_text((_COLUMNS["credit"], y), "Credit", fontsize=11)
    page.insert_text((_COLUMNS["balance"], y), "Balance", fontsize=11)
    y += 20
    for date, desc, debit, credit, balance in SAMPLE_TRANSACTIONS:
        page.insert_text((_COLUMNS["date"], y), date, fontsize=11)
        page.insert_text((_COLUMNS["desc"], y), desc, fontsize=11)
        page.insert_text((_COLUMNS["debit"], y), debit, fontsize=11)
        page.insert_text((_COLUMNS["credit"], y), credit, fontsize=11)
        page.insert_text((_COLUMNS["balance"], y), balance, fontsize=11)
        y += 16
    y += 14
    page.insert_text((50, y), "Opening Balance: 10000.00", fontsize=11)
    y += 15
    page.insert_text((50, y), "Closing Balance: 4300.00", fontsize=11)


def _draw_page2(page) -> None:
    y = 50
    sections = [
        ("Deposits and Other Credits", DEPOSIT_SUPPORTING),
        ("Withdrawals and Other Debits", [("10/22", "ATM WITHDRAWAL", "3000.00")]),
        ("Account Service Charges and Fees", [("11/09", "SERVICE CHARGE", "150.00")]),
        ("Checks Paid", CHECK_SUPPORTING),
    ]
    for title, rows in sections:
        page.insert_text((50, y), title, fontsize=11)
        y += 17
        for date, desc, amount in rows:
            page.insert_text((50, y), f"{date} {desc} {amount}", fontsize=11)
            y += 16
        y += 10


def build_scanned_statement_pdf() -> bytes:
    """Render the two pages as text then re-export them as an image-only PDF."""
    text_doc = pymupdf.open()
    _draw_page1(text_doc.new_page())
    _draw_page2(text_doc.new_page())
    pixmaps = [
        page.get_pixmap(matrix=pymupdf.Matrix(3, 3), alpha=False) for page in text_doc
    ]
    text_doc.close()

    scan = pymupdf.open()
    for pixmap in pixmaps:
        page = scan.new_page(width=pixmap.width, height=pixmap.height)
        page.insert_image(page.rect, pixmap=pixmap)
    data = scan.tobytes()
    scan.close()
    return data


@pytest.fixture(scope="session")
def scanned_sample_pdf() -> bytes:
    return build_scanned_statement_pdf()