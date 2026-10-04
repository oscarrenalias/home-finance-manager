"""CSV parser data structures for Finnish bank exports."""

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

PARSER_VERSION = "1.0"

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
