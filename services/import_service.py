"""Service-layer dataclasses for the CSV import pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from domain.balance_check import BalanceCheckResult
from domain.matching import MatchCandidate


@dataclass
class RowPreview:
    row_number: int
    display_text: str
    parsed_date: date
    amount_cents: int
    is_pending: bool
    match_candidate: MatchCandidate | None = None


@dataclass
class ImportPreview:
    file_hash: str
    parser_version: str
    idempotency_token: str
    already_imported: bool
    same_file_different_account: bool
    executed_count: int
    pending_count: int
    new_count: int
    exact_match_count: int
    probable_match_count: int
    ambiguous_count: int
    error_count: int
    row_previews: list[RowPreview] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    balance_check: BalanceCheckResult | None = None


@dataclass
class CommitResult:
    batch_id: str
    new_transactions: int
    reused_transactions: int
    pending_observations: int
    enqueued_jobs: int
    ambiguous_count: int
