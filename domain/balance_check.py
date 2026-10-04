"""Balance check data structures for import validation."""

from dataclasses import dataclass, field


@dataclass
class BalanceMismatch:
    row_number_a: int
    row_number_b: int
    expected_cents: int
    actual_cents: int


@dataclass
class BalanceCheckResult:
    checked_pairs: int
    mismatches: list[BalanceMismatch] = field(default_factory=list)
    inconclusive_reasons: list[str] = field(default_factory=list)
