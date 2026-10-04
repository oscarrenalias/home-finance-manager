"""CSV parser data structures for Finnish bank exports."""

from dataclasses import dataclass, field
from datetime import date

PARSER_VERSION = "1.0"


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
