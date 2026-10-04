"""CSV parser data structures for Finnish bank exports."""

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

PARSER_VERSION = "1.0"

EXPECTED_COLUMNS = [
    "Date",
    "Category",
    "Subcategory",
    "Text",
    "Amount",
    "Balance",
    "Status",
    "Reconciled",
]

_log = logging.getLogger(__name__)


def _parse_amount(raw: str) -> int:
    """Parse a Finnish-format decimal string to signed integer cents.

    Accepts decimal comma notation with optional thousands-separator dot.
    Examples: '-2,55' -> -255, '1.140,71' -> 114071
    """
    stripped = raw.strip()
    # Remove thousands-separator dots before the decimal comma
    # A thousands dot appears before a comma (or before 3+ digits followed by comma/end)
    normalised = stripped.replace(".", "").replace(",", ".")
    try:
        value = Decimal(normalised)
    except InvalidOperation:
        raise ValueError(f"Cannot parse amount: {raw!r}")
    cents = int(value * 100)
    # Guard against rounding artifacts from Decimal * 100
    if Decimal(cents) != value * 100:
        raise ValueError(f"Amount does not convert to exact cents: {raw!r}")
    return cents


def _parse_date(raw: str) -> date:
    """Parse a DD.MM.YYYY date string, raising ValueError on invalid input."""
    stripped = raw.strip()
    try:
        return date(int(stripped[6:10]), int(stripped[3:5]), int(stripped[0:2]))
    except (ValueError, IndexError):
        raise ValueError(f"Cannot parse date: {raw!r}")


def _clean_text(raw: str) -> str:
    """Strip trailing whitespace and cosmetic runs of ')' characters."""
    return re.sub(r"\)+$", "", raw.rstrip()).rstrip()


def _decode_bytes(data: bytes) -> str:
    """Decode bytes as UTF-8 (with BOM), falling back to latin-1 with a warning."""
    # utf-8-sig transparently strips the UTF-8 BOM if present
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        _log.warning("UTF-8 decoding failed; falling back to latin-1")
        return data.decode("latin-1")


@dataclass
class ParsedRow:
    row_number: int
    raw_date: str
    raw_category: str
    raw_subcategory: str
    raw_text: str
    raw_amount: str
    raw_balance: str
    raw_status: str
    raw_reconciled: str
    parsed_date: date
    parsed_amount_cents: int
    parsed_balance_cents: int | None
    is_pending: bool
    display_text: str
    parse_errors: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    rows: list[ParsedRow]
    header_errors: list[str]
    parser_version: str


def parse_csv(data: bytes) -> ParseResult:
    """Parse raw Finnish bank CSV bytes into a ParseResult.

    Handles UTF-8 BOM and falls back to latin-1.  Header column mismatch
    stops parsing early.  Per-row parse errors are collected on the row
    rather than raised.
    """
    if not data:
        return ParseResult(rows=[], header_errors=[], parser_version=PARSER_VERSION)

    text = _decode_bytes(data)
    reader = csv.reader(io.StringIO(text), delimiter=";")
    rows_iter = iter(reader)

    # Read and validate header
    try:
        raw_header = next(rows_iter)
    except StopIteration:
        return ParseResult(rows=[], header_errors=[], parser_version=PARSER_VERSION)

    header = [col.strip() for col in raw_header]

    header_errors: list[str] = []
    missing = [col for col in EXPECTED_COLUMNS if col not in header]
    extra = [col for col in header if col not in EXPECTED_COLUMNS]
    if missing:
        header_errors.append(f"Missing columns: {', '.join(missing)}")
    if extra:
        header_errors.append(f"Unexpected columns: {', '.join(extra)}")
    if header_errors:
        return ParseResult(rows=[], header_errors=header_errors, parser_version=PARSER_VERSION)

    col_idx = {col: header.index(col) for col in EXPECTED_COLUMNS}

    parsed_rows: list[ParsedRow] = []
    for line_number, raw_row in enumerate(rows_iter, start=2):
        if not raw_row or all(cell.strip() == "" for cell in raw_row):
            continue

        parse_errors: list[str] = []

        raw_date = raw_row[col_idx["Date"]]
        raw_category = raw_row[col_idx["Category"]]
        raw_subcategory = raw_row[col_idx["Subcategory"]]
        raw_text = raw_row[col_idx["Text"]]
        raw_amount = raw_row[col_idx["Amount"]]
        raw_balance = raw_row[col_idx["Balance"]]
        raw_status = raw_row[col_idx["Status"]]
        raw_reconciled = raw_row[col_idx["Reconciled"]]

        try:
            parsed_date = _parse_date(raw_date)
        except ValueError as exc:
            parse_errors.append(str(exc))
            parsed_date = date.min  # sentinel; consumer must check parse_errors

        try:
            parsed_amount_cents = _parse_amount(raw_amount)
        except ValueError as exc:
            parse_errors.append(str(exc))
            parsed_amount_cents = 0  # sentinel; consumer must check parse_errors

        balance_stripped = raw_balance.strip()
        if balance_stripped:
            try:
                parsed_balance_cents: int | None = _parse_amount(balance_stripped)
            except ValueError as exc:
                parse_errors.append(str(exc))
                parsed_balance_cents = None
        else:
            parsed_balance_cents = None

        status_stripped = raw_status.strip()
        is_pending = status_stripped == "Pending"

        display_text = _clean_text(raw_text)

        parsed_rows.append(
            ParsedRow(
                row_number=line_number,
                raw_date=raw_date,
                raw_category=raw_category,
                raw_subcategory=raw_subcategory,
                raw_text=raw_text,
                raw_amount=raw_amount,
                raw_balance=raw_balance,
                raw_status=raw_status,
                raw_reconciled=raw_reconciled,
                parsed_date=parsed_date,
                parsed_amount_cents=parsed_amount_cents,
                parsed_balance_cents=parsed_balance_cents,
                is_pending=is_pending,
                display_text=display_text,
                parse_errors=parse_errors,
            )
        )

    return ParseResult(rows=parsed_rows, header_errors=[], parser_version=PARSER_VERSION)
