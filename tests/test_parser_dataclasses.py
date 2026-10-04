"""Tests for ParsedRow and ParseResult dataclasses (B1 data structures)."""
from __future__ import annotations

import sys
from dataclasses import fields
from datetime import date


def test_domain_parser_importable_without_reflex():
    from domain import parser  # noqa: F401

    assert "reflex" not in sys.modules, "domain.parser must not import Reflex"


def test_parser_version_constant_is_non_empty():
    from domain.parser import PARSER_VERSION

    assert isinstance(PARSER_VERSION, str)
    assert PARSER_VERSION, "PARSER_VERSION must be a non-empty string"


def test_parsed_row_field_names():
    from domain.parser import ParsedRow

    expected = {
        "row_number",
        "raw_date",
        "raw_category",
        "raw_subcategory",
        "raw_text",
        "raw_amount",
        "raw_balance",
        "raw_status",
        "raw_reconciled",
        "parsed_date",
        "parsed_amount_cents",
        "parsed_balance_cents",
        "is_pending",
        "display_text",
        "parse_errors",
    }
    actual = {f.name for f in fields(ParsedRow)}
    assert actual == expected


def test_parse_result_field_names():
    from domain.parser import ParseResult

    expected = {"rows", "header_errors", "parser_version"}
    actual = {f.name for f in fields(ParseResult)}
    assert actual == expected


def test_parsed_row_instantiation_with_balance():
    from domain.parser import ParsedRow

    row = ParsedRow(
        row_number=1,
        raw_date="30.09.2026",
        raw_category="Ruoka",
        raw_subcategory="Päivittäistavarat",
        raw_text="K-MARKET JOENSUU",
        raw_amount="-12,50",
        raw_balance="1 140,71",
        raw_status="Executed",
        raw_reconciled="No",
        parsed_date=date(2026, 9, 30),
        parsed_amount_cents=-1250,
        parsed_balance_cents=114071,
        is_pending=False,
        display_text="K-MARKET JOENSUU",
    )
    assert row.row_number == 1
    assert row.parsed_amount_cents == -1250
    assert row.parsed_balance_cents == 114071
    assert row.is_pending is False
    assert row.parse_errors == []  # default_factory


def test_parsed_row_parse_errors_default_is_empty_list():
    from domain.parser import ParsedRow

    row = ParsedRow(
        row_number=2,
        raw_date="01.01.2026",
        raw_category="",
        raw_subcategory="",
        raw_text="SOME MERCHANT",
        raw_amount="-5,00",
        raw_balance="",
        raw_status="Pending",
        raw_reconciled="No",
        parsed_date=date(2026, 1, 1),
        parsed_amount_cents=-500,
        parsed_balance_cents=None,
        is_pending=True,
        display_text="SOME MERCHANT",
    )
    assert row.parse_errors == []
    # Verify it is a fresh list, not shared
    row.parse_errors.append("err")
    row2 = ParsedRow(
        row_number=3,
        raw_date="01.01.2026",
        raw_category="",
        raw_subcategory="",
        raw_text="OTHER",
        raw_amount="-1,00",
        raw_balance="",
        raw_status="Executed",
        raw_reconciled="No",
        parsed_date=date(2026, 1, 1),
        parsed_amount_cents=-100,
        parsed_balance_cents=None,
        is_pending=False,
        display_text="OTHER",
    )
    assert row2.parse_errors == [], "parse_errors must use default_factory, not a shared default"


def test_parsed_row_balance_can_be_none():
    from domain.parser import ParsedRow

    row = ParsedRow(
        row_number=1,
        raw_date="01.01.2026",
        raw_category="",
        raw_subcategory="",
        raw_text="PENDING TX",
        raw_amount="-50,00",
        raw_balance="",
        raw_status="Pending",
        raw_reconciled="No",
        parsed_date=date(2026, 1, 1),
        parsed_amount_cents=-5000,
        parsed_balance_cents=None,
        is_pending=True,
        display_text="PENDING TX",
    )
    assert row.parsed_balance_cents is None


def test_parse_result_instantiation():
    from domain.parser import ParseResult

    result = ParseResult(rows=[], header_errors=[], parser_version="1.0")
    assert result.rows == []
    assert result.header_errors == []
    assert result.parser_version == "1.0"


def test_parse_result_carries_parsed_rows():
    from domain.parser import ParsedRow, ParseResult

    row = ParsedRow(
        row_number=1,
        raw_date="30.09.2026",
        raw_category="",
        raw_subcategory="",
        raw_text="SHOP",
        raw_amount="-10,00",
        raw_balance="",
        raw_status="Executed",
        raw_reconciled="No",
        parsed_date=date(2026, 9, 30),
        parsed_amount_cents=-1000,
        parsed_balance_cents=None,
        is_pending=False,
        display_text="SHOP",
    )
    result = ParseResult(rows=[row], header_errors=[], parser_version="1.0")
    assert len(result.rows) == 1
    assert result.rows[0] is row


def test_parse_result_with_header_errors():
    from domain.parser import ParseResult

    result = ParseResult(
        rows=[],
        header_errors=["Unknown column: Extra"],
        parser_version="1.0",
    )
    assert len(result.header_errors) == 1


def test_parsed_row_with_parse_errors():
    from domain.parser import ParsedRow

    row = ParsedRow(
        row_number=5,
        raw_date="99.99.9999",
        raw_category="",
        raw_subcategory="",
        raw_text="BAD DATE",
        raw_amount="-1,00",
        raw_balance="",
        raw_status="Executed",
        raw_reconciled="No",
        parsed_date=date(2026, 1, 1),  # fallback placeholder
        parsed_amount_cents=-100,
        parsed_balance_cents=None,
        is_pending=False,
        display_text="BAD DATE",
        parse_errors=["Invalid date: 99.99.9999"],
    )
    assert len(row.parse_errors) == 1
    assert "99.99.9999" in row.parse_errors[0]
